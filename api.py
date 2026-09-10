"""SMM Service Performance Statistics API

Website ও AI দুটোর জন্যই আলাদা এন্ডপয়েন্ট। গুরুত্বপূর্ণ:
  /api/statistics/ai  → একটাই কলে পুরো সিস্টেমের সব ডেটা (AI-এর জন্য)
"""
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import chatbot
import config
import database as db
import orders_feed
import scheduler
import statistics_engine as engine

BASE_DIR = Path(__file__).resolve().parent


def _do_sync():
    """লাইভ Admin API থেকে ডেটা সিঙ্ক — API key আবশ্যক (কোনো ডেমো ফলব্যাক নেই)।"""
    if not config.API_KEY:
        raise RuntimeError(
            "SMM_ADMIN_API_KEY সেট করা নেই। .env ফাইলে আসল অ্যাডমিন এপিআই কী বসান — "
            "সিস্টেম শুধু লাইভ ডেটায় চলে, কোনো ডেমো মোড নেই।"
        )
    import collector
    return collector.full_sync()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # স্টার্টআপ: ডাটাবেজ রেডি + (দরকারে) ব্যাকগ্রাউন্ডে প্রথম সিঙ্ক + ডেইলি শিডিউলার
    db.init_db()
    conn = db.get_conn()
    try:
        has_data = db.count_orders(conn) > 0
    finally:
        conn.close()

    if not has_data:
        # প্রথম সিঙ্ক ব্যাকগ্রাউন্ড থ্রেডে — সার্ভার স্টার্টআপ ব্লক করে না, আর
        # লাইভ API সাময়িক আনরিচেবল হলেও সার্ভার ক্র্যাশ না করে চালু থাকে।
        def _initial_sync():
            try:
                print("[startup] প্রথম লাইভ সিঙ্ক শুরু হচ্ছে...")
                result = _do_sync()
                print(f"[startup] প্রথম সিঙ্ক সম্পন্ন: {result}")
            except Exception as exc:  # noqa: BLE001
                print(f"[startup] প্রথম সিঙ্ক ব্যর্থ (অটো/ম্যানুয়াল সিঙ্কে আবার চেষ্টা হবে): {exc}")

        threading.Thread(target=_initial_sync, name="initial-sync", daemon=True).start()

    scheduler.start_scheduler(_do_sync)
    yield


app = FastAPI(
    title="SMM Service Performance API",
    version="1.0.0",
    description="৯০ দিনের রোলিং ডেটা থেকে সার্ভিস পারফরম্যান্স স্কোর ও বেস্ট সার্ভিস র‍্যাংকিং",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# ড্যাশবোর্ডের services পেলোড ৬০০ KB+ হতে পারে — gzip-এ ~২৫ KB নামে,
# ধীর মোবাইল কানেকশনে এটাই লোড হওয়া আর টাইমআউটের পার্থক্য গড়ে দেয়।
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ---------------------------- ড্যাশবোর্ড ----------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (BASE_DIR / "dashboard.html").read_text(encoding="utf-8")


# ---------------------------- হেলথ চেক ----------------------------
@app.get("/api/health")
def api_health():
    """একদম হালকা লিভনেস চেক — কোনো অ্যাগ্রিগেশন করে না।

    Render-এর healthCheckPath আগে /api/status-এ ছিল, আর সেটা প্রতিবার
    পুরো পরিসংখ্যান হিসাব করে। ফ্রি প্ল্যানের 0.1 CPU-তে প্রথম সিঙ্ক
    চলাকালীন ওই চেকগুলো টাইমআউট করত, Render ডিপ্লয়টাকে ব্যর্থ ধরে
    রিস্টার্ট দিত — আর ড্যাশবোর্ড কখনো উঠতই না।
    """
    import collector
    return {
        "ok": True,
        "has_api_key": bool(config.API_KEY),
        "sync_running": collector.SYNC_STATE["running"],
        "sync_progress": collector.SYNC_STATE["fetched"],
    }


# ---------------------------- স্ট্যাটাস ----------------------------
@app.get("/api/status")
def api_status():
    import collector
    data = engine.get_statistics()
    return {
        "mode": "live" if config.API_KEY else "no-api-key",
        "sync_running": collector.SYNC_STATE["running"],
        "sync_progress": collector.SYNC_STATE["fetched"],
        "sync_started_at": collector.SYNC_STATE["started_at"],
        "api_base": config.API_BASE,
        "has_api_key": bool(config.API_KEY),
        "rolling_window_days": config.ROLLING_WINDOW_DAYS,
        "decay_halflife_days": config.DECAY_HALFLIFE_DAYS,
        "daily_sync_at": f"{config.DAILY_SYNC_HOUR:02d}:{config.DAILY_SYNC_MINUTE:02d}",
        "generated_at": data["generated_at"],
        "last_sync": data["last_sync"],
        "totals": data["totals"],
    }


# ---------------------------- 🤖 AI এন্ডপয়েন্ট (এক কলে সব) ----------------------------
@app.get("/api/statistics/ai")
def statistics_for_ai(
    platform: str | None = Query(None, description="শুধু নির্দিষ্ট প্ল্যাটফর্ম, যেমন: facebook"),
    limit_per_category: int = Query(3, ge=1, le=20, description="প্রতি ক্যাটাগরিতে কয়টি সার্ভিস"),
):
    """⭐ একটাই কলে পুরো সিস্টেমের সব ডেটা — AI রেকমেন্ডেশনের জন্য তৈরি।

    রিটার্ন করে: সামগ্রিক সারাংশ, প্রতিটি প্ল্যাটফর্ম → ক্যাটাগরি → বেস্ট সার্ভিস,
    প্রতিটি সার্ভিসের স্কোর/কমপ্লিশন রেট/ক্যানসেল রেট/গড় গতি, এবং টপ র‍্যাংকিং।
    """
    data = engine.get_statistics()
    platforms_out = {}

    for pname, pdata in data["platforms"].items():
        if platform and pname.lower() != platform.lower():
            continue
        categories_out = {}
        for cname, cdata in pdata["categories"].items():
            best = cdata.get("best_service")
            categories_out[cname] = {
                "total_orders": cdata["total_orders"],
                "total_services": len(cdata["ranking"]),
                "best_service": _compact(best) if best else None,
                "top_services": [_compact(s) for s in cdata["ranking"][:limit_per_category]],
            }
        platforms_out[pname] = {
            "total_orders": pdata["total_orders"],
            "total_services": pdata["services"],
            "categories": categories_out,
        }

    return {
        "system": "SMM Service Performance & Recommendation Engine",
        "usage": "AI should read 'platforms' → category → 'best_service' to recommend services to customers.",
        "generated_at": data["generated_at"],
        "rolling_window_days": data["rolling_window_days"],
        "totals": data["totals"],
        "top_services_overall": [_compact(s) for s in data["services"][:20]],
        "platforms": platforms_out,
    }


def _compact(s: dict, admin: bool = False) -> dict:
    """সার্ভিসের কমপ্যাক্ট তথ্য।

    ডিফল্টে কাস্টমারকে দেখানোর উপযোগী: আটকা বা issue-র শতাংশ যায় না, শুধু
    is_stuck ব্যাজ। admin=True দিলে ভেতরের সংখ্যাগুলোও আসে — ড্যাশবোর্ডের জন্য।
    """
    out = {
        "service_id": s["service_id"],
        "service_name": s["service_name"],
        "platform": s["platform"],
        "category": s["category"],
        "score": s["score"],
        "rank_in_category": s.get("rank_in_category"),
        "total_in_category": s.get("total_in_category"),
        "is_new": s.get("is_new"),
        "is_thin": s.get("is_thin"),
        "is_stuck": not s.get("available", True),
        "available": s.get("available"),
        "total_orders": s["total_orders"],
        "completed": s["completed"],
        "completion_rate_percent": s["completion_rate"],
        "median_delivery": s.get("median_delivery"),
        "p90_delivery": s.get("p90_delivery"),
        "median_hours": s.get("median_hours"),
        "p90_hours": s.get("p90_hours"),
        "bucket_delivery": s.get("bucket_delivery"),
        "price_per_1000": s.get("unit_price"),
        "total_quantity": s["total_quantity"],
        "days_since_last_order": s["days_since_last_order"],
    }
    if admin:
        out.update({
            "issue_rate_percent": s.get("issue_rate"),
            "stuck_rate_percent": s.get("stuck_rate"),
            "stuck_orders": s.get("stuck_orders"),
            "open_orders": s.get("open_orders"),
            "processing_now": s.get("processing"),
            "effective_orders": s.get("effective_orders"),
            "speed_index": s.get("speed_index"),
            "trend": s.get("trend"),
            "trend_delta": s.get("trend_delta"),
            "base_score": s.get("base_score"),
            "category_average": s.get("category_average"),
        })
    return out


# ---------------------------- Best Services ----------------------------
@app.get("/api/statistics/best-services")
def best_services(limit: int = Query(20, ge=1, le=100)):
    """সব প্ল্যাটফর্ম মিলিয়ে সেরা সার্ভিসগুলো (স্কোর অনুযায়ী)"""
    return {
        "count": limit,
        "best_services": [_compact(s) for s in engine.best_services_overall(limit)],
    }


@app.get("/api/statistics/services")
def all_services(
    platform: str | None = Query(None, description="প্ল্যাটফর্ম ফিল্টার, যেমন: facebook"),
    category: str | None = Query(None, description="ক্যাটাগরি ফিল্টার, যেমন: likes"),
    active_only: bool = Query(False, description="শুধু ব্যবহারযোগ্য সার্ভিস (বন্ধ/ভাঙা বাদ)"),
    admin: bool = Query(False, description="ড্যাশবোর্ডের জন্য ভেতরের সংখ্যাও দেবে"),
    limit: int = Query(1000, ge=1, le=10000),
):
    """সব সার্ভিসের পূর্ণ পরিসংখ্যান, স্কোর অনুযায়ী সাজানো — ড্যাশবোর্ড টেবিলের জন্য।"""
    data = engine.get_statistics()
    services = data["services"]
    if platform:
        services = [s for s in services if s["platform"].lower() == platform.lower()]
    if category:
        services = [s for s in services if s["category"].lower() == category.lower()]
    if active_only:
        services = [s for s in services if s.get("available")]
    sliced = services[:limit]
    return {
        "count": len(sliced),
        "total_available": len(data["services"]),
        "generated_at": data["generated_at"],
        "services": [_compact(s, admin=admin) for s in sliced],
    }


@app.get("/api/statistics/services/{service_id}")
def service_detail(service_id: int):
    """একটি সার্ভিসের পূর্ণ পরিসংখ্যান

    গুরুত্বপূর্ণ: এই রুট অবশ্যই /{platform}/{category} এর উপরে থাকতে হবে,
    নইলে 'services' কে platform ধরে নিয়ে ভুল রুটে চলে যাবে।
    """
    data = engine.get_statistics()
    for s in data["services"]:
        if s["service_id"] == service_id:
            # ভেতরের সংখ্যা (issue/stuck %) পাবলিক রেসপন্সে যায় না
            return _compact(s)
    raise HTTPException(status_code=404, detail=f"Service {service_id} পাওয়া যায়নি")


@app.get("/api/statistics/{platform}/best-services")
def platform_best_services(platform: str):
    """নির্দিষ্ট প্ল্যাটফর্মের প্রতিটি ক্যাটাগরির বেস্ট সার্ভিস"""
    result = engine.best_services_by_platform(platform)
    best = result.get("best_services") or {}
    # কাঁচা stats dict-এ রেভিনিউ থাকে — _compact দিয়ে ছেঁকে দেওয়া হয়
    result["best_services"] = {k: _compact(v) for k, v in best.items() if v}
    return result


@app.get("/api/statistics/{platform}/{category}")
def platform_category(platform: str, category: str, limit: int = Query(10, ge=1, le=50)):
    """নির্দিষ্ট প্ল্যাটফর্ম+ক্যাটাগরির পূর্ণ র‍্যাংকিং — যেমন /api/statistics/facebook/likes"""
    result = engine.platform_category_stats(platform, category)
    if result.get("ranking"):
        result["ranking"] = [_compact(s) for s in result["ranking"][:limit]]
        result["best_service"] = _compact(result["best_service"]) if result.get("best_service") else None
    return result


# ---------------------------- ✅ সম্পন্ন অর্ডার ফিড (পাবলিক) ----------------------------
@app.get("/api/orders/completed")
def orders_completed(
    service_id: int | None = Query(None, description="শুধু এই সার্ভিসের অর্ডার"),
    platform: str | None = Query(None, description="প্ল্যাটফর্ম ফিল্টার, যেমন: facebook"),
    search: str | None = Query(None, description="সার্ভিস নাম বা অর্ডার আইডি সার্চ"),
    limit: int = Query(20, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """সম্পন্ন অর্ডারের লাইভ ফিড — ওয়েবসাইটের Statistics পেজের জন্য।

    কোনো link, username, IP বা চার্জ ফেরত দেওয়া হয় না — শুধু পাবলিকে দেখানোর
    উপযোগী তথ্য (অর্ডার আইডি, সার্ভিস, পরিমাণ, কত সময়ে সম্পন্ন হয়েছে)।
    """
    return orders_feed.completed_orders(
        service_id=service_id, platform=platform, search=search,
        limit=limit, offset=offset,
    )


@app.get("/api/orders/completed/{service_id}")
def orders_completed_by_service(service_id: int, limit: int = Query(6, ge=1, le=50)):
    """একটি সার্ভিসের সাম্প্রতিক ডেলিভারি টাইম — New Order পেজের চিপগুলোর জন্য।

    যেমন: 4400 = 15h 4m · 5000 = 9m · 1000 = 12m
    """
    return orders_feed.service_delivery_times(service_id, limit)


# ---------------------------- 🛍️ পাবলিক সার্ভিস লিস্ট (কাস্টমারের জন্য) ----------------------------
def _public_service(s: dict) -> dict:
    """কাস্টমারকে দেখানোর উপযোগী ফিল্ড শুধু।

    ইচ্ছাকৃতভাবে বাদ: রেভিনিউ, issue %, stuck %, base score, trend।
    সার্ভিস আটকে থাকলে শুধু is_stuck = true যায় — কোনো সংখ্যা নয়।
    """
    return {
        "service_id": s["service_id"],
        "service_name": s["service_name"],
        "platform": s["platform"],
        "category": s["category"],
        "rating": s["score"],
        "rank_in_category": s.get("rank_in_category"),
        "total_in_category": s.get("total_in_category"),
        "is_new": s.get("is_new"),
        "is_stuck": not s.get("available", True),
        "delivery_rate_percent": s["completion_rate"],
        "typical_delivery": s.get("median_delivery"),
        "p90_delivery": s.get("p90_delivery"),
        "delivery_by_size": s.get("bucket_delivery"),
        "based_on_orders": s["total_orders"],
        "price_per_1000": s.get("unit_price"),
        "label": _service_label(s),
    }


def _service_label(s: dict) -> str:
    """কাস্টমারকে এক লাইনে দেখানোর মতো:  ID - Name - price"""
    parts = [str(s["service_id"]), s.get("service_name") or ""]
    if s.get("unit_price"):
        parts.append(f"{s['unit_price']:.4f}".rstrip("0").rstrip("."))
    return " - ".join(parts)


@app.get("/api/services/public")
def services_public(
    platform: str | None = Query(None, description="প্ল্যাটফর্ম ফিল্টার"),
    category: str | None = Query(None, description="ক্যাটাগরি ফিল্টার"),
    search: str | None = Query(None, description="নাম বা সার্ভিস আইডি"),
    sort: str = Query("rating", description="rating | fastest | popular | name"),
    min_orders: int = Query(0, ge=0, description="এর কম অর্ডারের সার্ভিস বাদ"),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """ওয়েবসাইটের পাবলিক 'Service performance' পেজের জন্য।

    কোনো রেভিনিউ বা ব্যবসার ভেতরের তথ্য এখানে যায় না।
    বন্ধ বা ভেঙে পড়া সার্ভিস আগেই বাদ পড়ে।
    """
    data = engine.get_statistics()
    rows = [s for s in data["services"] if s.get("available")]

    if platform:
        rows = [s for s in rows if s["platform"].lower() == platform.lower()]
    if category:
        rows = [s for s in rows if s["category"].lower() == category.lower()]
    if min_orders:
        rows = [s for s in rows if s["total_orders"] >= min_orders]
    if search:
        q = search.strip().lower()
        rows = [s for s in rows
                if q in (s["service_name"] or "").lower() or q in str(s["service_id"])]

    if sort == "fastest":
        rows.sort(key=lambda s: (s.get("median_hours") is None,
                                 s.get("median_hours") or 1e12, -s["score"]))
    elif sort == "popular":
        rows.sort(key=lambda s: (-s["total_orders"], -s["score"]))
    elif sort == "name":
        rows.sort(key=lambda s: (s["service_name"] or "").lower())
    else:
        rows.sort(key=lambda s: (-s["score"], -s["total_orders"]))

    sliced = rows[offset:offset + limit]

    platforms: dict = {}
    for s in rows:
        platforms[s["platform"]] = platforms.get(s["platform"], 0) + 1

    return {
        "count": len(sliced),
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(sliced) < len(rows),
        "sorted_by": sort,
        "window_days": data["rolling_window_days"],
        "generated_at": data["generated_at"],
        "platforms": [{"name": k, "services": v}
                      for k, v in sorted(platforms.items(), key=lambda x: -x[1])],
        "categories": sorted({s["category"] for s in rows}),
        "services": [_public_service(s) for s in sliced],
    }


# ---------------------------- ম্যানুয়াল সিঙ্ক ----------------------------
@app.post("/api/sync")
def sync_now():
    """ম্যানুয়ালি ডেটা আপডেট ট্রিগার করা"""
    try:
        result = _do_sync()
        if result.get("status") == "already-running":
            # স্টার্টআপ সিঙ্ক বা ডেইলি শিডিউলার এখনো চলছে — এটা এরর নয়।
            return {"ok": True, "busy": True, "result": result}
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


# ---------------------------- 🤖 AI চ্যাটবট ----------------------------
class _ChatTurn(BaseModel):
    role: str = "user"
    content: str = ""


class _ChatIn(BaseModel):
    message: str
    history: list[_ChatTurn] | None = None
    lang: str | None = None


# সহজ ইন-মেমরি রেট লিমিটার (Gemini কোটা রক্ষায়) — {ip: [timestamps]}
_rate_hits: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_ok(ip: str) -> bool:
    now = time.time()
    window = config.CHATBOT_RATE_WINDOW_SEC
    hits = [t for t in _rate_hits.get(ip, []) if now - t < window]
    if len(hits) >= config.CHATBOT_RATE_LIMIT:
        _rate_hits[ip] = hits
        return False
    hits.append(now)
    _rate_hits[ip] = hits
    if len(_rate_hits) > 5000:  # অতি-বৃদ্ধি ঠেকাতে হালকা পরিষ্কার
        for k in [k for k, v in _rate_hits.items() if not v or now - v[-1] > window]:
            _rate_hits.pop(k, None)
    return True


@app.get("/api/chatbot/init")
def chatbot_init():
    """উইজেটের কনফিগ — কোনো ডাটাবেজ/সেটআপ ছাড়াই সরাসরি config থেকে।"""
    return {
        "bot_name": config.BOT_NAME,
        "panel_name": config.PANEL_NAME,
        "panel_domain": config.PANEL_DOMAIN,
        "brand_color": config.BRAND_COLOR,
        "greeting": config.CHATBOT_GREETING,
        "greeting_interval_hours": config.CHATBOT_GREETING_INTERVAL_HOURS,
        "suggestions": config.CHATBOT_SUGGESTIONS,
        "currency": config.CURRENCY,
        "ai_enabled": bool(config.GEMINI_API_KEY),
    }


@app.post("/api/chatbot/message")
def chatbot_message(payload: _ChatIn, request: Request):
    """স্টেটলেস চ্যাট — ক্লায়েন্ট সাম্প্রতিক history পাঠায়, সার্ভার কোনো state রাখে না।"""
    ip = _client_ip(request)
    if not _rate_ok(ip):
        return JSONResponse(status_code=429, content={
            "reply": "You're sending messages a little fast — give me a few seconds and try again 🙏",
            "mode": "rate-limited",
        })

    message = (payload.message or "").strip()
    if not message:
        return JSONResponse(status_code=400, content={"error": "empty message"})
    message = message[:2000]

    history = [{"role": t.role, "content": (t.content or "")[:2000]}
               for t in (payload.history or []) if (t.content or "").strip()]
    history.append({"role": "user", "content": message})
    if len(history) > config.CHATBOT_HISTORY_TURNS:
        history = history[-config.CHATBOT_HISTORY_TURNS:]

    result = chatbot.chat(history, lang=payload.lang)
    return {
        "reply": result.get("reply", ""),
        "mode": result.get("mode"),
        "tools_used": result.get("tools_used", []),
    }


@app.get("/smm-stats.js")
def stats_js():
    """সম্পন্ন অর্ডার ফিড উইজেট — <script src=".../smm-stats.js" async></script>"""
    js = (BASE_DIR / "smm-stats.js").read_text(encoding="utf-8")
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/widget.js")
def widget_js():
    """এম্বেডযোগ্য উইজেট স্ক্রিপ্ট — <script src=".../widget.js" async></script>"""
    js = (BASE_DIR / "widget.js").read_text(encoding="utf-8")
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/chat", response_class=HTMLResponse)
def chat_demo():
    """উইজেট টেস্ট করার ডেমো পেজ।"""
    return (BASE_DIR / "chatbot_demo.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
