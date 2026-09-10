"""সম্পন্ন অর্ডারের পাবলিক ফিড — লাইভ স্ট্যাটিসটিকস পেজ ও সার্ভিস-ভিত্তিক ডেলিভারি টাইম।

নিরাপত্তা: এখানে ইচ্ছাকৃতভাবে link / username / ip_address / charge কিছুই ফেরত
দেওয়া হয় না। শুধু অর্ডার আইডি, সার্ভিস, পরিমাণ ও সময় — যা পাবলিকে দেখানো নিরাপদ।
"""
from datetime import datetime

import database as db


# ------------------------------ ফরম্যাটিং ------------------------------
def humanize_seconds(sec) -> str | None:
    """সেকেন্ড → '1m 1s' / '9m 32s' / '15h 4m' / '2d 3h'"""
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return None
    if sec < 0:
        return None
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        m, s = divmod(sec, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    if sec < 86400:
        h, rem = divmod(sec, 3600)
        m = rem // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d, rem = divmod(sec, 86400)
    h = rem // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def _iso(ts) -> str | None:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts > 0 else None


def _row_to_public(r) -> dict:
    sec = (r["last_update_timestamp"] or 0) - (r["created_timestamp"] or 0)
    return {
        "order_id": r["id"],
        "service_id": r["service_id"],
        "service_name": r["service_name"] or "",
        "platform": r["platform"] or "Other",
        "category": r["category"] or "Other",
        "quantity": r["quantity"] or 0,
        "completed_in": humanize_seconds(sec),
        "completed_in_seconds": sec if sec > 0 else None,
        "completed_at": _iso(r["last_update_timestamp"]),
        "completed_at_timestamp": r["last_update_timestamp"] or 0,
    }


# ------------------------------ কোয়েরি ------------------------------
_BASE_SQL = """
SELECT o.id, o.service_id, o.service_name, o.quantity,
       o.created_timestamp, o.last_update_timestamp,
       s.platform, s.category
FROM orders o
LEFT JOIN services s ON s.service_id = o.service_id
WHERE o.status = 'completed'
  AND o.last_update_timestamp > o.created_timestamp
"""


def completed_orders(service_id=None, platform=None, search=None,
                     limit=20, offset=0) -> dict:
    """সম্পন্ন অর্ডারের তালিকা — নতুনগুলো আগে।

    service_id দিলে শুধু ওই সার্ভিসের, নইলে সব সার্ভিসের।
    """
    where = ""
    params: list = []

    if service_id is not None:
        where += " AND o.service_id = ?"
        params.append(int(service_id))
    if platform:
        where += " AND LOWER(s.platform) = LOWER(?)"
        params.append(platform)
    if search:
        term = f"%{search.strip()}%"
        # অর্ডার আইডি, সার্ভিস আইডি বা সার্ভিসের নাম — তিনটাতেই সার্চ
        where += (" AND (o.service_name LIKE ? OR CAST(o.id AS TEXT) LIKE ?"
                  " OR CAST(o.service_id AS TEXT) LIKE ?)")
        params += [term, term, term]

    conn = db.get_conn()
    try:
        rows = conn.execute(
            _BASE_SQL + where + " ORDER BY o.last_update_timestamp DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        ).fetchall()
        orders = [_row_to_public(r) for r in rows]

        total = conn.execute(
            "SELECT COUNT(*) AS c FROM orders o "
            "LEFT JOIN services s ON s.service_id = o.service_id "
            "WHERE o.status = 'completed' "
            "  AND o.last_update_timestamp > o.created_timestamp" + where,
            params,
        ).fetchone()["c"]
    finally:
        conn.close()

    return {
        "count": len(orders),
        "total_completed": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(orders) < total,
        "orders": orders,
    }


def service_delivery_times(service_id: int, limit: int = 6) -> dict:
    """একটি সার্ভিসের সাম্প্রতিক সম্পন্ন ডেলিভারি টাইম (New Order পেজের চিপগুলোর জন্য)।"""
    data = completed_orders(service_id=service_id, limit=limit)
    orders = data["orders"]

    secs = [o["completed_in_seconds"] for o in orders if o["completed_in_seconds"]]
    avg = round(sum(secs) / len(secs)) if secs else None

    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(service_name) AS n, COUNT(*) AS c FROM orders "
            "WHERE service_id = ? AND status = 'completed'",
            (int(service_id),),
        ).fetchone()
    finally:
        conn.close()

    return {
        "service_id": int(service_id),
        "service_name": (row["n"] if row else "") or "",
        "completed_orders": (row["c"] if row else 0) or 0,
        "average_time": humanize_seconds(avg),
        "average_time_seconds": avg,
        "recent": [
            {
                "quantity": o["quantity"],
                "completed_in": o["completed_in"],
                "completed_in_seconds": o["completed_in_seconds"],
                "completed_at": o["completed_at"],
            }
            for o in orders
        ],
    }
