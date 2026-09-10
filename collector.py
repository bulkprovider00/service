"""Mothersmm Admin API v2 থেকে অর্ডার ডেটা কালেক্টর"""
import threading
import time
from datetime import datetime, timedelta

import requests

import config
import database as db
import platform_mapper
import statistics_engine

SESSION = requests.Session()


def _headers():
    return {"X-Api-Key": config.API_KEY, "Accept": "application/json"}


def _get_orders_page(offset: int, limit: int, created_from: int, created_to: int):
    """GET /orders — এক পেজ অর্ডার আনে (রেট লিমিট: ৫ রিকোয়েস্ট/সেকেন্ড)"""
    params = {
        "created_from": created_from,
        "created_to": created_to,
        "offset": offset,
        "limit": limit,
        "include": "last_update",
        "sort": "date-asc",
    }
    for attempt in range(6):
        resp = SESSION.get(f"{config.API_BASE}/orders", headers=_headers(), params=params, timeout=60)
        if resp.status_code == 429:  # রেট লিমিট হলে একটু অপেক্ষা করে আবার চেষ্টা
            time.sleep(1.2 * (attempt + 1))
            continue
        resp.raise_for_status()
        body = resp.json()
        if body.get("error_code"):
            raise RuntimeError(f"API error {body.get('error_code')}: {body.get('error_message')}")
        data = body.get("data") or {}
        return data.get("list") or []
    raise RuntimeError("Rate limited (429) — অনেকবার চেষ্টার পরেও ব্যর্থ")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def collect_services() -> list[dict]:
    """লাইভ সার্ভিস ক্যাটালগ — প্যানেলে এখন কোন সার্ভিসগুলো চালু আছে।

    Admin API-তে সার্ভিস লিস্ট নেই, তাই PerfectPanel-এর স্ট্যান্ডার্ড রিসেলার
    API v2 ব্যবহার করা হয়:  POST /api/v2  { key, action=services }

    এটাই একমাত্র নির্ভরযোগ্য উপায় ডিজেবল সার্ভিস শনাক্ত করার — অর্ডার ডেটা থেকে
    বোঝা যায় না, কারণ ডিজেবল সার্ভিসের পুরোনো অর্ডার ডাটাবেজে থেকেই যায়।

    key না থাকলে খালি লিস্ট (is_active অজানা থাকবে, কিছু ভাঙবে না)।
    """
    if not config.USER_API_KEY:
        print("[collector] SMM_USER_API_KEY নেই — সার্ভিস ক্যাটালগ সিঙ্ক বাদ")
        return []

    try:
        resp = SESSION.post(
            config.PUBLIC_API_URL,
            data={"key": config.USER_API_KEY, "action": "services"},
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[collector] সার্ভিস ক্যাটালগ আনা যায়নি: {exc}")
        return []

    if isinstance(body, dict):
        if body.get("error"):
            print(f"[collector] ক্যাটালগ API error: {body['error']}")
            return []
        body = body.get("services") or body.get("data") or []
    if not isinstance(body, list):
        return []

    out = []
    for s in body:
        if not isinstance(s, dict):
            continue
        sid = _int(s.get("service"))
        if sid is None:
            continue
        out.append({
            "service_id": sid,
            "name": s.get("name") or "",
            "type": s.get("type") or "",
            "panel_category": s.get("category") or "",
            "rate": _num(s.get("rate")),
            "min": _int(s.get("min")),
            "max": _int(s.get("max")),
        })
    print(f"[collector] লাইভ ক্যাটালগ: {len(out)} সার্ভিস চালু")
    return out


def _normalize_order(o: dict) -> dict:
    """API রেসপন্স → ডাটাবেজের সারি।

    গুরুত্বপূর্ণ: অর্ডারের মোট চার্জ কখনো সংরক্ষণ করা হয় না — অর্থাৎ রেভিনিউ
    ডাটাবেজে থাকেই না। config.STORE_UNIT_PRICE = True করলে শুধু প্রতি ১০০০
    ইউনিটের দাম রাখা হয়, যা প্যানেলের ক্যাটালগেই সবাই দেখতে পায়।
    """
    unit_price = None
    if config.STORE_UNIT_PRICE:
        charge = o.get("charge") or {}
        try:
            qty = int(o.get("quantity") or 0)
            val = float(charge.get("value") or 0)
            if qty > 0 and val > 0:
                unit_price = round(val * 1000.0 / qty, 6)
        except (TypeError, ValueError):
            unit_price = None
    currency = (o.get("charge") or {}).get("currency_code") or ""
    return {
        "id": int(o.get("id")),
        "external_id": o.get("external_id") or "",
        "username": o.get("user") or "",
        "creation_type": o.get("creation_type") or "",
        "unit_price": unit_price,
        "currency": currency,
        "link": o.get("link") or "",
        "start_count": int(o.get("start_count") or 0),
        "quantity": int(o.get("quantity") or 0),
        "service_id": int(o.get("service_id") or 0),
        "service_type": o.get("service_type") or "",
        "service_name": o.get("service_name") or "",
        "provider": o.get("provider") or "",
        "status": o.get("status") or "",
        "remains": int(o.get("remains") or 0),
        "mode": o.get("mode") or "",
        "ip_address": o.get("ip_address") or "",
        "created_timestamp": int(o.get("created_timestamp") or 0),
        "last_update_timestamp": int(o.get("last_update_timestamp") or 0),
    }


def iter_order_pages(days: int | None = None):
    """অর্ডারগুলো পেজ ধরে ধরে yield করে — পুরোটা একসাথে মেমোরিতে রাখে না।

    আগে পুরো ১২০ দিনের অর্ডার একটা লিস্টে জমিয়ে, তারপর আরেকটা normalized
    লিস্ট বানানো হতো — অর্থাৎ পিক মেমোরিতে দুইটা কপি। বড় প্যানেলে
    (৫ লাখ+ অর্ডার) এতে ৬০০ MB+ লাগে, আর Render ফ্রি প্ল্যানের ৫১২ MB
    লিমিটে প্রসেসটা OOM-kill হয়ে রিস্টার্ট লুপে পড়ে যায়। পেজ ধরে ধরে
    দিলে যেকোনো সময়ে মাত্র ১০০০টা অর্ডার মেমোরিতে থাকে।
    """
    window = days or config.ROLLING_WINDOW_DAYS
    now = datetime.utcnow()
    created_from = int((now - timedelta(days=window)).timestamp())
    created_to = int(now.timestamp())

    PAGE = 1000        # API-এর সর্বোচ্চ লিমিট
    MAX_PAGES = 5000   # সেফটি ব্রেক — API ভুল করে সবসময় ফুল পেজ দিলেও লুপ থামবে
    offset = 0
    for _ in range(MAX_PAGES):
        page = _get_orders_page(offset, PAGE, created_from, created_to)
        if page:
            yield [_normalize_order(o) for o in page]
        if len(page) < PAGE:
            return
        offset += PAGE
        time.sleep(0.25)  # রেট লিমিট মেনে চলা
    print(f"[collector] MAX_PAGES ({MAX_PAGES}) ছুঁয়ে ফেলেছে — সিঙ্ক এখানেই থামানো হলো")


def collect_orders(days: int | None = None) -> list[dict]:
    """সব অর্ডার একটা লিস্টে (পুরনো কল-সাইটের জন্য রাখা)।

    ⚠️ বড় প্যানেলে এটা অনেক মেমোরি নেয় — সিঙ্কের জন্য iter_order_pages() ব্যবহার করুন।
    """
    out: list[dict] = []
    for page in iter_order_pages(days):
        out.extend(page)
    return out


# সিঙ্কের লাইভ অবস্থা — /api/status এটা দেখায়, যাতে ড্যাশবোর্ড
# "মরে গেছে" মনে না হয়ে "চলছে, এতগুলো অর্ডার এসেছে" দেখাতে পারে।
SYNC_STATE: dict = {"running": False, "fetched": 0, "started_at": None}

# একসাথে একটার বেশি সিঙ্ক নয়। স্টার্টআপের ব্যাকগ্রাউন্ড সিঙ্ক, ডেইলি
# শিডিউলার আর "Sync Now" বাটন — তিনটাই full_sync() ডাকে। দুইটা একসাথে
# চললে SQLite-এ "database is locked" আসে, তাই দ্বিতীয়টা চুপচাপ ফিরে যায়।
_SYNC_LOCK = threading.Lock()


def full_sync(days: int | None = None) -> dict:
    """পূর্ণ সিঙ্ক: API → ডাটাবেজ → পরিসংখ্যান রিফ্রেশ (রোলিং ১২০ দিন)"""
    if not _SYNC_LOCK.acquire(blocking=False):
        return {"status": "already-running",
                "fetched": SYNC_STATE["fetched"],
                "message": "আরেকটা সিঙ্ক এখনো চলছে — সেটা শেষ হওয়ার অপেক্ষা করুন"}

    window = days or config.ROLLING_WINDOW_DAYS
    started = datetime.utcnow()
    SYNC_STATE.update(running=True, fetched=0,
                      started_at=started.strftime("%Y-%m-%d %H:%M:%S UTC"))
    conn = db.get_conn()
    try:
        # পেজ ধরে ধরে: আনো → সাথে সাথে বসাও → commit → মেমোরি ছেড়ে দাও।
        # প্রতি পেজেই commit — নাহলে API কল আর rate-limit sleep-এর পুরোটা
        # সময় write transaction খোলা থাকে আর বাকি সব রিকোয়েস্ট লক খায়।
        fetched = upserted = 0
        for page in iter_order_pages(window):
            upserted += db.upsert_orders(conn, page)
            conn.commit()
            fetched += len(page)
            SYNC_STATE["fetched"] = fetched
            if fetched % 20000 == 0:
                print(f"[collector] {fetched:,} অর্ডার সিঙ্ক হয়েছে…")
        cutoff = int((datetime.utcnow() - timedelta(days=window)).timestamp())
        pruned = db.prune_old_orders(conn, cutoff)
        db.refresh_services(conn, platform_mapper.detect_platform_category)

        # লাইভ ক্যাটালগ — কোন সার্ভিস এখনো চালু আছে
        catalog = collect_services()
        marks = db.sync_service_catalog(conn, catalog, platform_mapper.detect_platform_category)

        db.bump_data_version(conn)
        db.log_sync(conn, "success", fetched, upserted,
                    f"window={window}d, pruned={pruned}, "
                    f"active={marks['active']}, inactive={marks['inactive']}")
        conn.commit()
        statistics_engine.invalidate_cache()
        return {
            "status": "success",
            "fetched": fetched,
            "upserted": upserted,
            "pruned_old": pruned,
            "services_active": marks["active"],
            "services_inactive": marks["inactive"],
            "window_days": window,
            "duration_sec": round((datetime.utcnow() - started).total_seconds(), 2),
        }
    except Exception as exc:  # noqa: BLE001
        db.log_sync(conn, "error", 0, 0, str(exc)[:500])
        conn.commit()
        raise
    finally:
        SYNC_STATE.update(running=False)
        conn.close()
        _SYNC_LOCK.release()
