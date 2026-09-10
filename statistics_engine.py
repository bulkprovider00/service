"""Statistics Engine — সার্ভিসের পারফরম্যান্স স্কোর, র‍্যাংকিং ও ট্রেন্ড।

স্কোরিং (০–১০০), শুধু নিষ্পত্তি হওয়া অর্ডারের উপর:

  ৫৫%  completion   — completed / settled  (চলমান অর্ডার ব্যর্থতা নয়)
  ১০%  issues       — cancel + partial একসাথে (ভুল লিংক/কাস্টমার ক্যানসেলও এতে পড়ে)
  ২০%  stuck        — আটকে থাকা অর্ডার
  ০৫%  volume       — কত অর্ডারের ভিত্তিতে বলছি
  ১০%  speed        — নিজের ক্যাটাগরির তুলনায় গতি

  তারপর আটকা হার অনুযায়ী গুণক (০.১২ পর্যন্ত নামতে পারে)
  তারপর ক্যাটাগরির গড়ের দিকে টান (prior K) — নতুন সার্ভিস শূন্য থেকে শুরু করে না

সাম্প্রতিকতা: প্রতিটা অর্ডারের ওজন 0.5 ^ (বয়স / half-life)।
পুরনো অর্ডার বাদ যায় না, শুধু হালকা হয়।
"""
import math
import time
from datetime import datetime

import config
import database as db

# ------------------------------ ক্যাশ ------------------------------
_CACHE: dict = {"stats": None, "version": None, "ts": 0}
_CACHE_TTL = 300  # সেকেন্ড


def invalidate_cache() -> None:
    _CACHE["stats"] = None
    _CACHE["version"] = None


# ------------------------------ ছোট হেল্পার ------------------------------
def _decay_sql(alias: str = "o") -> str:
    """অর্ডারের বয়স অনুযায়ী ওজন — SQL এক্সপ্রেশন।"""
    hl = max(0.5, config.DECAY_HALFLIFE_DAYS)
    return (f"POWER(0.5, (strftime('%s','now') - {alias}.created_timestamp)"
            f" / 86400.0 / {hl})")


def _percentile(values: list[float], p: float):
    """সাজানো তালিকা থেকে p-তম পার্সেন্টাইল (p = 0.5 মানে মিডিয়ান)।"""
    if not values:
        return None
    v = sorted(values)
    i = int(round(p * (len(v) - 1)))
    return v[max(0, min(len(v) - 1, i))]


def humanize_hours(h):
    """ঘণ্টা → '12m' / '2h 30m' / '1d 4h'"""
    if h is None or h <= 0:
        return None
    sec = int(h * 3600)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        m, s = divmod(sec, 60)
        return f"{m}m {s}s" if s and m < 5 else f"{m}m"
    if sec < 86400:
        hh, rem = divmod(sec, 3600)
        m = rem // 60
        return f"{hh}h {m}m" if m else f"{hh}h"
    d, rem = divmod(sec, 86400)
    hh = rem // 3600
    return f"{d}d {hh}h" if hh else f"{d}d"


def size_bucket(qty: int) -> str:
    for lo, hi, label in config.SIZE_BUCKETS:
        if qty >= lo and (hi is None or qty < hi):
            return label
    return config.SIZE_BUCKETS[-1][2]


def stuck_multiplier(rate: float) -> float:
    for limit, mult in config.STUCK_MULTIPLIERS:
        if rate <= limit:
            return mult
    return config.STUCK_MULTIPLIER_WORST


# ------------------------------ কাঁচা ডেটা ------------------------------
def _weighted_aggregates(conn) -> dict:
    """সার্ভিস অনুযায়ী ক্ষয়-ওজনসহ গণনা।"""
    w = _decay_sql("o")
    sql = f"""
    SELECT
        o.service_id                                   AS service_id,
        MAX(s.service_name)                            AS service_name,
        MAX(s.platform)                                AS platform,
        MAX(s.category)                                AS category,
        MAX(s.is_active)                               AS is_active,
        COUNT(*)                                       AS raw_total,
        SUM({w})                                       AS w_total,
        SUM(CASE WHEN o.status='completed'  THEN {w} ELSE 0 END)  AS w_completed,
        SUM(CASE WHEN o.status='canceled'   THEN {w} ELSE 0 END)  AS w_canceled,
        SUM(CASE WHEN o.status='partial'    THEN {w} ELSE 0 END)  AS w_partial,
        SUM(CASE WHEN o.status IN ('fail','error') THEN {w} ELSE 0 END) AS w_failed,
        SUM(CASE WHEN o.status='completed'  THEN 1 ELSE 0 END)    AS completed,
        SUM(CASE WHEN o.status='canceled'   THEN 1 ELSE 0 END)    AS canceled,
        SUM(CASE WHEN o.status='partial'    THEN 1 ELSE 0 END)    AS partial,
        SUM(CASE WHEN o.status IN ('fail','error') THEN 1 ELSE 0 END) AS failed,
        SUM(CASE WHEN o.status='pending'    THEN 1 ELSE 0 END)    AS pending,
        SUM(CASE WHEN o.status='processing' THEN 1 ELSE 0 END)    AS processing,
        SUM(CASE WHEN o.status='in_progress' THEN 1 ELSE 0 END)   AS in_progress,
        SUM(o.quantity)                                AS total_quantity,
        AVG(o.unit_price)                              AS unit_price,
        MAX(o.created_timestamp)                       AS last_order_ts,
        MIN(o.created_timestamp)                       AS first_order_ts,
        SUM(CASE WHEN o.status='pending'
                  AND (strftime('%s','now') - o.created_timestamp)
                      > {config.STUCK_PENDING_HOURS} * 3600 THEN 1 ELSE 0 END)   AS stuck_pending,
        SUM(CASE WHEN o.status='processing'
                  AND (strftime('%s','now') - o.created_timestamp)
                      > {config.STUCK_PROCESSING_HOURS} * 3600 THEN 1 ELSE 0 END) AS stuck_processing,
        SUM(CASE WHEN o.status='in_progress'
                  AND (strftime('%s','now') - o.created_timestamp)
                      > {config.STUCK_IN_PROGRESS_HOURS} * 3600 THEN 1 ELSE 0 END) AS stuck_in_progress
    FROM orders o
    LEFT JOIN services s ON s.service_id = o.service_id
    GROUP BY o.service_id
    """
    return {r["service_id"]: dict(r) for r in conn.execute(sql).fetchall()}


def _delivery_samples(conn) -> dict:
    """সম্পন্ন অর্ডারের সময় — সার্ভিস ও সাইজ-বাকেট অনুযায়ী।"""
    rows = conn.execute("""
        SELECT service_id, quantity,
               (last_update_timestamp - created_timestamp) / 3600.0 AS hours
        FROM orders
        WHERE status = 'completed'
          AND last_update_timestamp > created_timestamp
    """).fetchall()

    out: dict = {}
    for r in rows:
        sid = r["service_id"]
        b = size_bucket(int(r["quantity"] or 0))
        d = out.setdefault(sid, {"all": [], "buckets": {}})
        d["all"].append(r["hours"])
        d["buckets"].setdefault(b, []).append(r["hours"])
    return out


def _window_rates(conn, start_days: int, end_days: int) -> dict:
    """একটি সময়-জানালায় completion rate — Trend হিসাবের জন্য।"""
    sql = """
    SELECT service_id,
           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS ok,
           SUM(CASE WHEN status IN ('completed','canceled','partial','fail','error')
                    THEN 1 ELSE 0 END)                         AS settled
    FROM orders
    WHERE created_timestamp >= strftime('%s','now') - ? * 86400
      AND created_timestamp <  strftime('%s','now') - ? * 86400
    GROUP BY service_id
    """
    out = {}
    for r in conn.execute(sql, (end_days, start_days)).fetchall():
        if r["settled"]:
            out[r["service_id"]] = (r["ok"] / r["settled"], r["settled"])
    return out


# ------------------------------ স্কোর ------------------------------
def compute_base_score(m: dict) -> float:
    """ওজন যোগ করে base, তারপর আটকার গুণক। prior এখানে নয়।"""
    if m["settled"] <= 0:
        return 0.0
    base = (config.W_COMPLETION * m["completion"]
            + config.W_ISSUES * (1 - m["issues"])
            + config.W_STUCK * (1 - m["stuck_rate"])
            + config.W_VOLUME * m["volume"]
            + config.W_SPEED * m["speed"])
    return max(0.0, min(1.0, base)) * stuck_multiplier(m["stuck_rate"]) * 100


def build_metrics(row: dict, samples: dict) -> dict:
    """একটা সার্ভিসের সব কাঁচা সংখ্যা → পরিমাপ।"""
    w_settled = ((row["w_completed"] or 0) + (row["w_canceled"] or 0)
                 + (row["w_partial"] or 0) + (row["w_failed"] or 0))
    open_now = ((row["pending"] or 0) + (row["processing"] or 0)
                + (row["in_progress"] or 0))
    stuck = ((row["stuck_pending"] or 0) + (row["stuck_processing"] or 0)
             + (row["stuck_in_progress"] or 0))
    all_orders = (row["raw_total"] or 0)

    completion = (row["w_completed"] or 0) / w_settled if w_settled else 0.0
    issues = ((row["w_canceled"] or 0) + (row["w_partial"] or 0)) / w_settled if w_settled else 0.0
    stuck_rate = stuck / all_orders if all_orders else 0.0
    volume = min(1.0, math.log10(w_settled + 1) / 3.0) if w_settled else 0.0

    times = samples.get(row["service_id"], {"all": [], "buckets": {}})
    median_h = _percentile(times["all"], 0.5)
    p90_h = _percentile(times["all"], 0.9)

    return {
        "settled": w_settled,
        "open_now": open_now,
        "stuck": stuck,
        "stuck_rate": stuck_rate,
        "completion": completion,
        "issues": issues,
        "volume": volume,
        "median_hours": median_h,
        "p90_hours": p90_h,
        "bucket_medians": {b: _percentile(v, 0.5) for b, v in times["buckets"].items()},
        "speed": 0.5,   # ক্যাটাগরি জানার পর ভরা হয়
    }


def _speed_from_index(index: float | None) -> float:
    """ক্যাটাগরির তুলনায় গতি → ০–১।

    index = ক্যাটাগরির মিডিয়ান / নিজের মিডিয়ান।
    ১.০ = ক্যাটাগরির সমান, ২.০ = দ্বিগুণ দ্রুত, ০.৫ = অর্ধেক ধীর।
    """
    if index is None or index <= 0:
        return 0.5
    return max(0.0, min(1.0, 0.5 + math.log10(index) / 1.2))


# ------------------------------ পূর্ণ পরিসংখ্যান ------------------------------
def get_statistics(force: bool = False) -> dict:
    conn = db.get_conn()
    try:
        version = db.get_data_version(conn)
        if (not force and _CACHE["stats"] is not None
                and _CACHE["version"] == version
                and time.time() - _CACHE["ts"] < _CACHE_TTL):
            return _CACHE["stats"]

        rows = _weighted_aggregates(conn)
        samples = _delivery_samples(conn)
        recent = _window_rates(conn, 0, config.TREND_RECENT_DAYS)
        older = _window_rates(conn, config.TREND_RECENT_DAYS, config.TREND_PRIOR_DAYS)

        metrics = {sid: build_metrics(r, samples) for sid, r in rows.items()}

        # --- ক্যাটাগরির মিডিয়ান সময় (সাইজ-বাকেট অনুযায়ী) ---
        cat_bucket: dict = {}
        for sid, r in rows.items():
            key = (r["platform"] or "Other", r["category"] or "Other")
            for b, med in metrics[sid]["bucket_medians"].items():
                if med:
                    cat_bucket.setdefault(key, {}).setdefault(b, []).append(med)
        cat_bucket_median = {
            k: {b: _percentile(v, 0.5) for b, v in bs.items()}
            for k, bs in cat_bucket.items()
        }

        # --- গতি: নিজের ক্যাটাগরির সাথে তুলনা ---
        for sid, r in rows.items():
            key = (r["platform"] or "Other", r["category"] or "Other")
            peers = cat_bucket_median.get(key, {})
            idx = [peers[b] / med for b, med in metrics[sid]["bucket_medians"].items()
                   if med and peers.get(b)]
            metrics[sid]["speed_index"] = sum(idx) / len(idx) if idx else None
            metrics[sid]["speed"] = _speed_from_index(metrics[sid]["speed_index"])

        # --- base স্কোর ---
        for sid in rows:
            metrics[sid]["base_score"] = compute_base_score(metrics[sid])

        # --- ক্যাটাগরির গড় (prior এর ভিত্তি) ---
        cat_scores: dict = {}
        for sid, r in rows.items():
            key = (r["platform"] or "Other", r["category"] or "Other")
            if metrics[sid]["settled"] >= 1:
                cat_scores.setdefault(key, []).append(metrics[sid]["base_score"])
        cat_avg = {k: sum(v) / len(v) for k, v in cat_scores.items()}
        overall_avg = (sum(cat_avg.values()) / len(cat_avg)) if cat_avg else 70.0

        # --- চূড়ান্ত স্কোর: prior দিয়ে টান ---
        services = []
        for sid, r in rows.items():
            m = metrics[sid]
            key = (r["platform"] or "Other", r["category"] or "Other")
            prior = cat_avg.get(key, overall_avg)
            n = m["settled"]
            score = round((n * m["base_score"] + config.PRIOR_K * prior)
                          / (n + config.PRIOR_K), 1) if (n + config.PRIOR_K) else 0.0

            # Trend — দুই জানালাতেই যথেষ্ট অর্ডার থাকলে তবেই।
            # নইলে ১-২টা অর্ডারে ±৬৬% এর মতো অর্থহীন সংখ্যা আসে।
            now_pair, before_pair = recent.get(sid), older.get(sid)
            trend_delta = None
            trend = "unknown"
            if now_pair and before_pair:
                (now_rate, now_n), (before_rate, before_n) = now_pair, before_pair
                if (now_n >= config.TREND_MIN_ORDERS
                        and before_n >= config.TREND_MIN_ORDERS):
                    trend_delta = round((now_rate - before_rate) * 100, 1)
                    if trend_delta >= config.TREND_THRESHOLD:
                        trend = "improving"
                    elif trend_delta <= -config.TREND_THRESHOLD:
                        trend = "declining"
                    else:
                        trend = "stable"

            last_ts = r["last_order_ts"] or 0
            # NEW = সত্যিই নতুন (কাঁচা অর্ডার, টেবিলের Orders কলামের সাথেই মেলে)
            # THIN = পুরনো কিন্তু সাম্প্রতিক প্রমাণ কম (ক্ষয়-ওজন দেওয়া)
            is_new = (r["raw_total"] or 0) < config.NEW_SERVICE_MAX_ORDERS
            is_thin = (not is_new) and n < config.THIN_EVIDENCE_MAX
            stuck_ok = m["stuck_rate"] < 0.10

            services.append({
                "service_id": sid,
                "service_name": r["service_name"],
                "platform": r["platform"] or "Other",
                "category": r["category"] or "Other",
                "score": score,
                "base_score": round(m["base_score"], 1),
                "category_average": round(prior, 1),
                "is_new": is_new,
                "is_thin": is_thin,
                "trend": trend,
                "trend_delta": trend_delta,
                "total_orders": r["raw_total"] or 0,
                "effective_orders": round(n, 1),
                "completed": r["completed"] or 0,
                "canceled": r["canceled"] or 0,
                "partial": r["partial"] or 0,
                "failed": r["failed"] or 0,
                "pending": r["pending"] or 0,
                "processing": r["processing"] or 0,
                "in_progress": r["in_progress"] or 0,
                "open_orders": m["open_now"],
                "stuck_orders": m["stuck"],
                "stuck_rate": round(m["stuck_rate"] * 100, 1),
                "completion_rate": round(m["completion"] * 100, 1),
                "issue_rate": round(m["issues"] * 100, 1),
                "median_hours": round(m["median_hours"], 3) if m["median_hours"] else None,
                "p90_hours": round(m["p90_hours"], 3) if m["p90_hours"] else None,
                "median_delivery": humanize_hours(m["median_hours"]),
                "p90_delivery": humanize_hours(m["p90_hours"]),
                "speed_index": round(m["speed_index"], 2) if m.get("speed_index") else None,
                "bucket_delivery": {b: humanize_hours(v)
                                    for b, v in m["bucket_medians"].items() if v},
                "total_quantity": r["total_quantity"] or 0,
                "unit_price": round(r["unit_price"], 4) if r["unit_price"] else None,
                "is_active": None if r.get("is_active") is None else bool(r["is_active"]),
                "available": (r["is_active"] != 0) and stuck_ok,
                "last_order": (datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M")
                               if last_ts else None),
                "days_since_last_order": (round((time.time() - last_ts) / 86400, 1)
                                          if last_ts else None),
            })

        services.sort(key=lambda s: (-s["score"], -s["total_orders"]))

        # --- প্ল্যাটফর্ম → ক্যাটাগরি গ্রুপ + ক্যাটাগরির ভেতরে র‍্যাংক ---
        platforms: dict = {}
        for s in services:
            p = platforms.setdefault(s["platform"],
                                     {"total_orders": 0, "services": 0, "categories": {}})
            p["total_orders"] += s["total_orders"]
            p["services"] += 1
            c = p["categories"].setdefault(s["category"],
                                           {"total_orders": 0, "services": []})
            c["total_orders"] += s["total_orders"]
            c["services"].append(s)

        for pdata in platforms.values():
            for cdata in pdata["categories"].values():
                ranked = sorted(cdata["services"],
                                key=lambda s: (-s["score"], -s["total_orders"]))
                total_in_cat = len(ranked)
                for i, s in enumerate(ranked, 1):
                    s["rank_in_category"] = i
                    s["total_in_category"] = total_in_cat
                live = [s for s in ranked if s["available"]]
                proven = [s for s in live if not s["is_new"] and not s["is_thin"]]
                cdata["ranking"] = ranked
                cdata["best_service"] = (proven[0] if proven
                                         else (live[0] if live else None))

        totals = {
            "total_orders": sum(s["total_orders"] for s in services),
            "total_services": len(services),
            "completed": sum(s["completed"] for s in services),
            "processing": sum(s["processing"] for s in services),
            "pending": sum(s["pending"] for s in services),
            "partial": sum(s["partial"] for s in services),
            "canceled": sum(s["canceled"] for s in services),
            "failed": sum(s["failed"] for s in services),
            "platforms": len(platforms),
            "services_available": sum(1 for s in services if s["available"]),
            "services_unavailable": sum(1 for s in services if not s["available"]),
            "services_new": sum(1 for s in services if s["is_new"]),
            "services_thin": sum(1 for s in services if s["is_thin"]),
            "services_declining": sum(1 for s in services if s["trend"] == "declining"),
            "services_improving": sum(1 for s in services if s["trend"] == "improving"),
        }

        result = {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "rolling_window_days": config.ROLLING_WINDOW_DAYS,
            "decay_halflife_days": config.DECAY_HALFLIFE_DAYS,
            "last_sync": db.last_sync(conn),
            "totals": totals,
            "services": services,
            "platforms": platforms,
        }
        _CACHE.update(stats=result, version=version, ts=time.time())
        return result
    finally:
        conn.close()


# ------------------------------ হেল্পার ------------------------------
def best_services_overall(limit: int = 20) -> list[dict]:
    return get_statistics()["services"][:limit]


def best_services_by_platform(platform: str) -> dict:
    data = get_statistics()
    for pname, pdata in data["platforms"].items():
        if pname.lower() == platform.lower():
            out = {}
            for cname, cdata in pdata["categories"].items():
                if cdata.get("best_service"):
                    out[cname] = cdata["best_service"]
            return {"platform": pname, "best_services": out}
    return {"platform": platform, "best_services": {},
            "note": "No data for this platform"}


def platform_category_stats(platform: str, category: str) -> dict:
    def norm(x):
        return "".join(ch for ch in (x or "").lower() if ch.isalnum())

    data = get_statistics()
    for pname, pdata in data["platforms"].items():
        if pname.lower() == platform.lower():
            for cname, cdata in pdata["categories"].items():
                if norm(cname) == norm(category):
                    return {
                        "platform": pname,
                        "category": cname,
                        "total_orders": cdata["total_orders"],
                        "total_services": len(cdata["ranking"]),
                        "best_service": cdata.get("best_service"),
                        "ranking": cdata["ranking"],
                    }
    return {"platform": platform, "category": category, "note": "No data found"}
