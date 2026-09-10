"""SQLite ডাটাবেজ — ৯০ দিনের রোলিং অর্ডার স্টোর (আলাদা ডেডিকেটেড ডেটাবেজ)"""
import sqlite3
from pathlib import Path

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    external_id TEXT,
    username TEXT,
    creation_type TEXT,
    unit_price REAL,          -- প্রতি ১০০০ ইউনিট দাম (ঐচ্ছিক)। মোট চার্জ কখনো নয়।
    currency TEXT,
    link TEXT,
    start_count INTEGER,
    quantity INTEGER,
    service_id INTEGER,
    service_type TEXT,
    service_name TEXT,
    provider TEXT,
    status TEXT,
    remains INTEGER,
    mode TEXT,
    ip_address TEXT,
    created_timestamp INTEGER,
    last_update_timestamp INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_service ON orders (service_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders (created_timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);

CREATE TABLE IF NOT EXISTS services (
    service_id INTEGER PRIMARY KEY,
    service_name TEXT,
    service_type TEXT,
    platform TEXT,
    category TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    status TEXT NOT NULL,
    orders_fetched INTEGER DEFAULT 0,
    orders_upserted INTEGER DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


# স্কিমা এই প্রসেসে একবার তৈরি হয়েছে কি না।
# ডাটাবেজ ফাইল মুছে গেলে বা init_db() ছাড়া কেউ কানেকশন চাইলেও যেন না ভাঙে।
_schema_ready = False


def _tune(conn: sqlite3.Connection) -> None:
    """লকিং সেটিংস — একটা লেখক + অনেক পাঠক একসাথে চালানোর জন্য।

    ডিফল্ট rollback-journal মোডে লেখক আর পাঠক একে অপরকে ব্লক করে: সিঙ্ক
    যখন লিখছে তখন ড্যাশবোর্ডের রিড আটকায়, আর রিড চলাকালীন সিঙ্কের commit
    আটকে গিয়ে ৫ সেকেন্ড পর "database is locked" দেয়। WAL মোডে দুইটা
    পাশাপাশি চলে।
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")   # ৫s → ৩০s
        conn.execute("PRAGMA synchronous=NORMAL")   # WAL-এ নিরাপদ, ডিস্কে অনেক দ্রুত
    except sqlite3.Error:
        pass   # কোনো কারণে PRAGMA না বসলেও কানেকশন কাজ করবে


def get_conn() -> sqlite3.Connection:
    global _schema_ready
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    _tune(conn)
    if not _schema_ready:
        _schema_ready = True          # রিকার্শন ঠেকাতে আগেই সেট
        try:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            conn.commit()
        except Exception:             # noqa: BLE001 — init_db() পরে আবার চেষ্টা করবে
            _schema_ready = False
    return conn


def init_db() -> None:
    global _schema_ready
    _schema_ready = False             # সবসময় নতুন করে চালানো হয়
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
        _schema_ready = True
    finally:
        conn.close()


# ---------- মাইগ্রেশন ----------
# পুরোনো ডাটাবেজে services টেবিলে এই কলামগুলো নেই — চুপচাপ যোগ করা হয়।
_SERVICE_COLUMNS = {
    "is_active": "INTEGER",      # 1 = লাইভ ক্যাটালগে আছে, 0 = ডিজেবল/মুছে ফেলা, NULL = অজানা
    "rate": "REAL",              # প্যানেলের বর্তমান দাম (প্রতি ১০০০)
    "min_qty": "INTEGER",
    "max_qty": "INTEGER",
    "catalog_synced_at": "TEXT",  # সর্বশেষ কবে লাইভ ক্যাটালগে দেখা গেছে
}


def _migrate(conn) -> None:
    # --- পুরনো ডাটাবেজ থেকে রেভিনিউ কলাম মুছে ফেলা ---
    ocols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    if "unit_price" not in ocols:
        conn.execute("ALTER TABLE orders ADD COLUMN unit_price REAL")
    if "charge" in ocols:
        # SQLite 3.35+ এ DROP COLUMN আছে; না থাকলে অন্তত ফাঁকা করে দেওয়া হয়
        try:
            conn.execute("ALTER TABLE orders DROP COLUMN charge")
        except Exception:  # noqa: BLE001
            conn.execute("UPDATE orders SET charge = NULL")

    have = {r["name"] for r in conn.execute("PRAGMA table_info(services)")}
    for col, coltype in _SERVICE_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE services ADD COLUMN {col} {coltype}")


# ---------- ডেটা ভার্সন (ক্যাশ ইনভ্যালিডেশনের জন্য) ----------
def get_data_version(conn) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'data_version'").fetchone()
    return int(row["value"]) if row else 0


def bump_data_version(conn) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('data_version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )


# ---------- অর্ডার ----------
def upsert_orders(conn, rows) -> int:
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO orders (id, external_id, username, creation_type, unit_price, currency,
                            link, start_count, quantity, service_id, service_type,
                            service_name, provider, status, remains, mode, ip_address,
                            created_timestamp, last_update_timestamp)
        VALUES (:id, :external_id, :username, :creation_type, :unit_price, :currency,
                :link, :start_count, :quantity, :service_id, :service_type,
                :service_name, :provider, :status, :remains, :mode, :ip_address,
                :created_timestamp, :last_update_timestamp)
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            remains = excluded.remains,
            unit_price = excluded.unit_price,
            service_name = excluded.service_name,
            service_type = excluded.service_type,
            last_update_timestamp = excluded.last_update_timestamp
        """,
        rows,
    )
    return len(rows)


def prune_old_orders(conn, min_created_timestamp: int) -> int:
    """রোলিং উইন্ডোর বাইরের পুরোনো অর্ডার মুছে ফেলা"""
    cur = conn.execute("DELETE FROM orders WHERE created_timestamp < ?", (min_created_timestamp,))
    return cur.rowcount or 0


def count_orders(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]


# ---------- সার্ভিস ম্যাপিং ----------
def refresh_services(conn, detector) -> None:
    """অর্ডার থেকে সার্ভিস টেবিল তৈরি + প্ল্যাটফর্ম/ক্যাটাগরি ম্যাপিং।

    DELETE করা হয় না — লাইভ ক্যাটালগ থেকে আসা is_active/rate ইত্যাদি যেন না মোছে।
    """
    rows = conn.execute(
        "SELECT service_id, MAX(service_name) AS service_name, MAX(service_type) AS service_type "
        "FROM orders GROUP BY service_id"
    ).fetchall()
    out = []
    for r in rows:
        platform, category = detector(r["service_name"] or "", r["service_type"] or "")
        out.append((r["service_id"], r["service_name"], r["service_type"], platform, category))
    conn.executemany(
        """
        INSERT INTO services (service_id, service_name, service_type, platform, category)
        VALUES (?,?,?,?,?)
        ON CONFLICT(service_id) DO UPDATE SET
            service_name = excluded.service_name,
            service_type = excluded.service_type,
            platform     = excluded.platform,
            category     = excluded.category
        """,
        out,
    )


def sync_service_catalog(conn, catalog, detector) -> dict:
    """লাইভ /services ক্যাটালগ থেকে কোন সার্ভিস এখনো চালু আছে সেটা চিহ্নিত করা।

    catalog: [{service_id, name, type, rate, min, max}, ...]
    ক্যাটালগে থাকলে is_active=1, না থাকলে (কিন্তু অর্ডার আছে) is_active=0।
    """
    if not catalog:
        return {"active": 0, "inactive": 0}

    rows = []
    for s in catalog:
        sid = s.get("service_id")
        if sid is None:
            continue
        name = s.get("name") or ""
        stype = s.get("type") or ""
        # প্যানেলের নিজের ক্যাটাগরি নাম থাকলে সেটাও ম্যাচিংয়ে কাজে লাগে
        hint = (s.get("panel_category") or "").strip()
        platform, category = detector(f"{name} {hint}".strip(), stype)
        rows.append((sid, name, stype, platform, category,
                     s.get("rate"), s.get("min"), s.get("max")))

    conn.executemany(
        """
        INSERT INTO services (service_id, service_name, service_type, platform, category,
                              rate, min_qty, max_qty, is_active, catalog_synced_at)
        VALUES (?,?,?,?,?,?,?,?,1,datetime('now'))
        ON CONFLICT(service_id) DO UPDATE SET
            service_name      = excluded.service_name,
            service_type      = excluded.service_type,
            platform          = excluded.platform,
            category          = excluded.category,
            rate              = excluded.rate,
            min_qty           = excluded.min_qty,
            max_qty           = excluded.max_qty,
            is_active         = 1,
            catalog_synced_at = excluded.catalog_synced_at
        """,
        rows,
    )

    live_ids = [r[0] for r in rows]
    marks = ",".join("?" * len(live_ids))
    cur = conn.execute(
        f"UPDATE services SET is_active = 0 WHERE service_id NOT IN ({marks})", live_ids
    )
    return {"active": len(rows), "inactive": cur.rowcount or 0}


def catalog_is_synced(conn) -> bool:
    """লাইভ ক্যাটালগ কখনো সিঙ্ক হয়েছে কিনা — না হলে is_active অর্থহীন (সব NULL)।"""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM services WHERE catalog_synced_at IS NOT NULL"
    ).fetchone()
    return (row["c"] if row else 0) > 0


# ---------- সিঙ্ক লগ ----------
def log_sync(conn, status: str, fetched: int, upserted: int, message: str = "") -> None:
    conn.execute(
        "INSERT INTO sync_log (run_at, status, orders_fetched, orders_upserted, message) "
        "VALUES (datetime('now'), ?, ?, ?, ?)",
        (status, fetched, upserted, message),
    )


def last_sync(conn):
    row = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
