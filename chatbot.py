"""🤖 AI চ্যাটবটের মস্তিষ্ক — Google Gemini (function-calling) + লাইভ অ্যানালিটিক্স।

ডিজাইন লক্ষ্য:
  • আসল মানুষের মতো, স্বাভাবিক ও উষ্ণ কথোপকথন — ব্যবহারকারীর ভাষা/স্ক্রিপ্ট মিলিয়ে।
  • সব উত্তর আসল ডেটায় ভিত্তি করে (হ্যালুসিনেশন নয়): statistics_engine থেকে সরাসরি।
  • ৩টি টুল: search_services, get_service_info, calculate_order_cost।
  • অর্ডার ট্র্যাকিং নেই — এই বট শুধু সার্ভিসের তথ্য ও দাম নিয়ে কাজ করে।
  • GEMINI_API_KEY না থাকলেও উইজেট চলে — নিয়ম-ভিত্তিক fallback মোডে।

Gemini REST কল করা হয় requests দিয়ে (কোনো অতিরিক্ত ডিপেন্ডেন্সি নেই)।
"""
import json
import re

import requests

import config
import statistics_engine

_SESSION = requests.Session()

# functionResponse কনটেন্টের role — generativelanguage API-তে user/model বৈধ;
# function response ইনপুট-সাইড, তাই "user"। (একটাই জায়গায় রাখা হলো যাতে দরকারে বদলানো সহজ)
_FUNC_ROLE = "user"


class GeminiError(Exception):
    pass


# ============================================================================
#  মুদ্রা ও ফরম্যাটিং হেল্পার
# ============================================================================
_CURRENCY_SYMBOLS = {
    "USD": "$", "BDT": "৳", "INR": "₹", "EUR": "€", "GBP": "£",
    "PKR": "₨", "NGN": "₦", "BRL": "R$", "RUB": "₽",
}


def _sym(cur=None) -> str:
    cur = (cur or config.CURRENCY or "USD").upper()
    return _CURRENCY_SYMBOLS.get(cur, cur + " ")


def _fmt_price(value, cur=None):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    txt = f"{value:.2f}" if value >= 1 else f"{value:.4f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return f"{_sym(cur)}{txt}"


def humanize_hours(h):
    """ঘণ্টা → মানুষ-পাঠযোগ্য সময় (2h 30m / 1d 4h / 45m)।"""
    if not h or h <= 0:
        return None
    if h < 1:
        return f"{int(round(h * 60))}m"
    if h < 24:
        hh = int(h)
        mm = int(round((h - hh) * 60))
        return f"{hh}h {mm}m" if mm else f"{hh}h"
    d = int(h // 24)
    hh = int(round(h - d * 24))
    return f"{d}d {hh}h" if hh else f"{d}d"


# ============================================================================
#  প্ল্যাটফর্ম/ক্যাটাগরি ম্যাচিং
# ============================================================================
_PLATFORM_ALIASES = {
    "ig": "instagram", "insta": "instagram", "instagram": "instagram",
    "fb": "facebook", "facebook": "facebook", "meta": "facebook",
    "yt": "youtube", "youtube": "youtube",
    "tt": "tiktok", "tiktok": "tiktok", "tik": "tiktok",
    "tw": "twitter", "twitter": "twitter", "x": "twitter",
    "tg": "telegram", "telegram": "telegram", "tele": "telegram",
    "wa": "whatsapp", "whatsapp": "whatsapp",
    "spotify": "spotify", "linkedin": "linkedin", "threads": "threads",
    "snapchat": "snapchat", "snap": "snapchat", "discord": "discord",
    "twitch": "twitch", "soundcloud": "soundcloud", "google": "google",
    "website": "website", "web": "website",
}


def _norm_platform(p):
    p = (p or "").strip().lower()
    return _PLATFORM_ALIASES.get(p, p)


def _platform_matches(target, service_platform):
    t = _norm_platform(target)
    s = (service_platform or "").lower()
    if not t:
        return True
    return t in s or s in t


def _category_matches(target, service_category):
    t = re.sub(r"[^a-z]", "", (target or "").lower())
    s = re.sub(r"[^a-z]", "", (service_category or "").lower())
    if not t:
        return True
    t, s = t.rstrip("s"), s.rstrip("s")
    return bool(t) and (t in s or s in t)


def _detect_platform(text):
    for w in re.split(r"[^a-z]+", (text or "").lower()):
        if w in _PLATFORM_ALIASES:
            return _PLATFORM_ALIASES[w]
    return None


# ============================================================================
#  টুল #1 — search_services (রেকমেন্ডেশন + দাম, লাইভ অ্যানালিটিক্স থেকে)
# ============================================================================
_STOP_WORDS = {
    "the", "for", "best", "top", "good", "service", "services", "me", "a", "an",
    "of", "get", "buy", "cheap", "cheapest", "fast", "fastest", "quality", "which",
    "what", "give", "need", "want", "some", "please", "and", "to", "is", "are",
    "my", "your", "with", "on", "recommend", "suggest", "show", "find", "any",
    "real", "active", "high",
    # Banglish/Bengali filler words — mustn't be treated as content keywords
    "koto", "kto", "dam", "daam", "koto", "konta", "kon", "kemon", "kmn",
    "valo", "bhalo", "vhalo", "sera", "niye", "jonno", "chai", "lagbe", "dibo",
    "dile", "kore", "korte", "ache", "ase", "ki", "ki", "amar", "ami", "apni",
    "tumi", "ekta", "ta", "tao", "kotodur", "koto",
}


def _sort_key(sort_by):
    if sort_by == "speed":
        return lambda s: (s.get("median_hours") is None,
                          s.get("median_hours") or 1e12, -(s.get("score") or 0))
    if sort_by == "price_low":
        return lambda s: (s.get("unit_price") is None,
                          s.get("unit_price") or 1e12, -(s.get("score") or 0))
    if sort_by == "popular":
        return lambda s: (-(s.get("total_orders") or 0), -(s.get("score") or 0))
    return lambda s: (-(s.get("score") or 0), -(s.get("total_orders") or 0))  # quality


def _service_card(s):
    price = s.get("unit_price")
    label = " - ".join(filter(None, [
        str(s.get("service_id")), s.get("service_name") or "",
        (f"{price:.4f}".rstrip("0").rstrip(".") if price else None)]))
    return {
        "label": label,          # "15122 - Instagram Likes | ... - 0.0751"
        "service_id": s.get("service_id"),
        "service_name": s.get("service_name"),
        "platform": s.get("platform"),
        "category": s.get("category"),
        "quality_score": s.get("score"),
        "completion_rate_percent": s.get("completion_rate"),
        "is_stuck": not s.get("available", True),
        "typical_delivery": s.get("median_delivery") or "varies",
        "p90_delivery": s.get("p90_delivery"),
        "delivery_by_size": s.get("bucket_delivery"),
        "price_per_1000": _fmt_price(s.get("unit_price")),
        "price_per_1000_value": s.get("unit_price"),
        "min_order": s.get("min_qty"),
        "max_order": s.get("max_qty"),
        "rank_in_category": s.get("rank_in_category"),
        "total_in_category": s.get("total_in_category"),
        "is_new": s.get("is_new"),
        "based_on_orders": s.get("total_orders"),
    }


def search_services(query=None, platform=None, category=None, sort_by="quality", limit=5):
    try:
        limit = max(1, min(int(limit or 5), 10))
    except (TypeError, ValueError):
        limit = 5
    sort_by = (sort_by or "quality").lower()
    if sort_by not in ("quality", "speed", "price_low", "popular"):
        sort_by = "quality"

    data = statistics_engine.get_statistics()
    services = data.get("services", [])

    q = (query or "").strip().lower()

    # কাস্টমার শুধু একটা সার্ভিস আইডি লিখলে সরাসরি সেটাই ফেরত
    bare_id = re.fullmatch(r"#?\s*(\d{2,})", q)
    if bare_id:
        hit = _find_service(bare_id.group(1))
        if hit:
            return {
                "results": [_service_card(hit)], "count": 1, "total_matching": 1,
                "sorted_by": "service_id", "currency": config.CURRENCY,
                "data_window_days": data.get("rolling_window_days"),
                "note": "Matched by service ID.",
            }

    keywords = [w for w in re.split(r"[^a-z0-9]+", q)
                if len(w) > 1 and not w.isdigit() and w not in _STOP_WORDS]

    # query থেকে প্ল্যাটফর্ম আঁচ করা (যদি আলাদা করে না দেওয়া থাকে)
    if not platform:
        for token in keywords:
            if token in _PLATFORM_ALIASES:
                platform = token
                break

    content_kw = [k for k in keywords if k not in _PLATFORM_ALIASES and len(k) >= 3]

    def matches(s):
        # বন্ধ/ভেঙে পড়া সার্ভিস কখনো রেকমেন্ড করা হয় না
        if not s.get("available", True):
            return False
        if platform and not _platform_matches(platform, s.get("platform")):
            return False
        if category and not _category_matches(category, s.get("category")):
            return False
        if content_kw:
            hay = f"{(s.get('service_name') or '').lower()} {(s.get('category') or '').lower()}"
            if not all(k in hay for k in content_kw):
                return False
        return True

    filtered = [s for s in services if matches(s)]

    # কড়া ম্যাচে কিছু না পেলে — ANY-কীওয়ার্ডে রিল্যাক্স
    if not filtered and content_kw:
        def loose(s):
            if not s.get("available", True):
                return False
            if platform and not _platform_matches(platform, s.get("platform")):
                return False
            hay = f"{(s.get('service_name') or '').lower()} {(s.get('category') or '').lower()}"
            return any(k in hay for k in content_kw)
        filtered = [s for s in services if loose(s)]

    if not filtered:
        return {
            "results": [], "count": 0, "currency": config.CURRENCY,
            "message": "No matching services found. Try a broader term like "
                       "'instagram followers', or ask about a specific platform.",
        }

    tested = [s for s in filtered if (s.get("total_orders") or 0) >= config.MIN_ORDERS_FOR_RANK]
    rest = [s for s in filtered if (s.get("total_orders") or 0) < config.MIN_ORDERS_FOR_RANK]
    keyfn = _sort_key(sort_by)
    tested.sort(key=keyfn)
    rest.sort(key=keyfn)
    ranked = (tested + rest)[:limit]

    return {
        "results": [_service_card(s) for s in ranked],
        "count": len(ranked),
        "total_matching": len(filtered),
        "sorted_by": sort_by,
        "currency": config.CURRENCY,
        "data_window_days": data.get("rolling_window_days"),
        "note": "Ranked from real orders in the panel's recent history. Prices are "
                "approximate averages; the exact price is shown at checkout.",
    }


# ============================================================================
#  টুল #2 — get_service_info (সার্ভিস আইডি দিয়ে সার্ভিসের তথ্য)
# ============================================================================
def _find_service(service_id):
    """স্ট্যাট থেকে সার্ভিসটা খুঁজে বের করা। না পেলে None।"""
    try:
        sid = int(str(service_id).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None
    for s in statistics_engine.get_statistics().get("services", []):
        if s.get("service_id") == sid:
            return s
    return None


def is_known_service(value) -> bool:
    """সংখ্যাটা কি আসলেই আমাদের একটা সার্ভিস আইডি?

    এটাই সিদ্ধান্তের ভিত্তি — কাস্টমার খালি একটা নম্বর লিখলে সেটা সার্ভিস আইডি
    নাকি অর্ডার আইডি, তা অনুমান না করে ডেটা দেখে বলা হয়।
    """
    return _find_service(value) is not None


def get_service_info(service_id):
    """একটি সার্ভিসের তথ্য — দাম, গতি, নির্ভরযোগ্যতা, সাম্প্রতিক ডেলিভারি টাইম।

    এটা অর্ডার লুকআপ নয়। কাস্টমার সার্ভিস আইডি দিলে এটাই ব্যবহার হয়।
    """
    s = _find_service(service_id)
    if not s:
        return {
            "ok": False,
            "message": f"I couldn't find service {service_id}. Double-check the ID, "
                       "or tell me what you're looking for and I'll find it.",
        }

    card = _service_card(s)
    card["ok"] = True
    card["available"] = s.get("available", True)
    card["min_order"] = s.get("min_qty")
    card["max_order"] = s.get("max_qty")

    # সাম্প্রতিক কয়টা অর্ডার কত সময়ে শেষ হয়েছে — সবচেয়ে কাজের প্রমাণ
    try:
        import orders_feed
        recent = orders_feed.service_delivery_times(s["service_id"], limit=5)
        card["recent_deliveries"] = [
            {"quantity": r["quantity"], "completed_in": r["completed_in"]}
            for r in recent.get("recent", []) if r.get("completed_in")
        ]
    except Exception:  # noqa: BLE001
        card["recent_deliveries"] = []

    if not card["available"]:
        card["warning"] = ("This service currently has orders stuck without delivering. "
                           "Tell the customer honestly and offer a better one from the "
                           "same category instead.")
    card["note"] = ("Performance is measured from real orders on this panel. "
                    "The exact price is shown at checkout.")
    return card


# ============================================================================
#  টুল #3 — calculate_order_cost
# ============================================================================
def calculate_order_cost(quantity, price_per_1000=None, service_query=None):
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return {"ok": False, "message": "Please tell me the quantity you want (e.g. 5000)."}
    if qty <= 0:
        return {"ok": False, "message": "The quantity should be a positive number."}

    service_name = None
    price = None
    if price_per_1000 is not None:
        try:
            price = float(price_per_1000)
        except (TypeError, ValueError):
            price = None
    if price is None and service_query:
        res = search_services(query=service_query, limit=1, sort_by="quality")
        if res.get("results"):
            top = res["results"][0]
            price = top.get("price_per_1000_value")
            service_name = top.get("service_name")
    if price is None:
        return {"ok": False,
                "message": "I need either a price per 1000 or a specific service name "
                           "to estimate the cost."}

    cost = round(price * qty / 1000.0, 4)
    return {
        "ok": True,
        "service_name": service_name,
        "quantity": qty,
        "price_per_1000": _fmt_price(price),
        "estimated_cost": _fmt_price(cost),
        "estimated_cost_value": cost,
        "currency": config.CURRENCY,
        "note": "Approximate estimate based on recent order prices. "
                "The exact price is shown at checkout.",
    }


# ============================================================================
#  টুল ডিসপ্যাচ
# ============================================================================
def dispatch_tool(name, args):
    args = args or {}
    try:
        if name == "search_services":
            return search_services(
                query=args.get("query"), platform=args.get("platform"),
                category=args.get("category"), sort_by=args.get("sort_by"),
                limit=args.get("limit", 5),
            )
        if name == "calculate_order_cost":
            return calculate_order_cost(
                quantity=args.get("quantity"),
                price_per_1000=args.get("price_per_1000"),
                service_query=args.get("service_query"),
            )
        if name == "get_service_info":
            return get_service_info(args.get("service_id"))
        return {"error": f"unknown tool: {name}"}
    except Exception as exc:  # noqa: BLE001 — টুল ব্যর্থ হলেও চ্যাট যেন না ভাঙে
        return {"error": str(exc)}


# ============================================================================
#  Gemini function declarations
# ============================================================================
TOOLS = [{
    "functionDeclarations": [
        {
            "name": "search_services",
            "description": (
                "Find and rank the panel's real services by performance. Call this "
                "whenever the user asks which service is best, wants a recommendation, "
                "or asks about pricing/rates for a platform or service. Returns ranked "
                "services with a quality score, completion rate, average delivery time, "
                "and average price per 1000 — all computed from recent real orders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text keywords, e.g. 'instagram followers', 'youtube views', 'tiktok likes'. Optional if platform/category are given."},
                    "platform": {"type": "string", "description": "Platform filter: instagram, facebook, tiktok, youtube, telegram, twitter, etc. Optional."},
                    "category": {"type": "string", "description": "Category filter: followers, likes, views, comments, subscribers, members, etc. Optional."},
                    "sort_by": {"type": "string", "enum": ["quality", "speed", "price_low", "popular"], "description": "Ranking: 'quality' (default, best overall), 'speed' (fastest), 'price_low' (cheapest), 'popular' (most ordered)."},
                    "limit": {"type": "integer", "description": "How many to return, 1-10. Default 5 — give the customer five options unless they ask for fewer."},
                },
            },
        },
        {
            "name": "calculate_order_cost",
            "description": (
                "Estimate the cost of an order. Provide quantity and either an explicit "
                "price_per_1000 or a service_query to look up the typical price. Use for "
                "any 'how much / what will it cost' question that involves a quantity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "quantity": {"type": "integer", "description": "Number of units wanted, e.g. 5000."},
                    "price_per_1000": {"type": "number", "description": "Price per 1000 units, if already known."},
                    "service_query": {"type": "string", "description": "Keywords to look up the price, e.g. 'instagram followers'. Used when price_per_1000 is not given."},
                },
                "required": ["quantity"],
            },
        },
        {
            "name": "get_service_info",
            "description": (
                "Look up ONE service by its service ID and return what that service "
                "does, its price, typical delivery time, reliability and recent "
                "delivery times. Use this whenever the customer mentions a service "
                "ID or asks about a specific service — a bare number is a SERVICE ID "
                "unless the customer explicitly says it is an order. This is NOT an "
                "order lookup and returns nothing about anyone's order."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "integer", "description": "The numeric service ID, e.g. 29018."},
                },
                "required": ["service_id"],
            },
        },
    ]
}]


# ============================================================================
#  System prompt — human-like persona + panel knowledge
# ============================================================================
def build_system_prompt(lang=None):
    return f"""You are {config.BOT_NAME}, a friendly and sharp live support agent for \
{config.PANEL_NAME} — an SMM (social media marketing) panel at {config.PANEL_DOMAIN}. \
You chat with customers on the website's support widget.

# How you talk (this matters most)
- Sound like a real, warm human on live chat — never robotic or corporate.
- ENGLISH IS THE DEFAULT. Open in English and stay in English unless the customer \
writes to you in another language.
- The moment a customer writes in another language, switch to that language and script \
for the rest of the chat. Banglish (Bangla in English letters) → natural Banglish. \
Bangla script → Bangla. Hindi, Arabic, Spanish → the same. Mirror them exactly; never \
switch languages on your own initiative, and never answer in a language they did not use.
- Keep it short and natural — usually 1-3 sentences, like texting. Use contractions. \
Vary your wording. A light emoji now and then is fine, but not every message.
- Be genuinely helpful and a little proactive: after answering, offer the obvious next step.
- Don't over-apologize or pad with filler. Get to the point kindly.

# Honesty
- You're {config.PANEL_NAME}'s virtual assistant. Don't pretend to be a human if a \
customer directly asks — just say you're the panel's assistant and you're happy to help. \
Stay warm either way.

# Use real data — never guess
- For ANY question about which service is best, recommendations, quality, speed, or \
pricing/rates → call `search_services` and answer only from what it returns. Never \
invent service names, IDs, prices, or numbers.
- For a cost estimate that involves a quantity → call `calculate_order_cost`.
- If the customer mentions a service ID or sends a bare number → call `get_service_info`.
- You have NO access to orders, accounts or balances, and no way to track anything. \
If a customer asks about their order, say so plainly in one line and point them to \
My Orders on the site or a support ticket — then offer what you CAN do (find a service, \
look up a service ID, estimate a cost). Never ask for an order ID.
- Every bare number a customer sends is a SERVICE ID. Look it up with `get_service_info`.
- If a tool returns nothing, say so honestly and suggest a broader search or ask a \
quick clarifying question. Never fabricate a result.

# Turning data into a good answer
- Recommend by name, with one or two concrete reasons and, when you have it, the price. \
Keep it skimmable.
- Rankings are WITHIN a category, which is what the customer cares about. Say \
"#1 of 9 Instagram Followers services", never "best on the whole panel".
- When you list services, use the `label` field exactly as given — it is already \
formatted as "ID - Service name - price". Give FIVE options unless the customer asks \
for fewer or the search returned less.
- Never quote internal reliability percentages other than the delivered rate. Do not \
mention issue rates, stuck rates, scores out of 100, or anything about how ranking \
works internally.
- Delivery time comes as two numbers: a typical time and a 9-out-of-10 time. Give the \
typical one, and add the second when it is much larger — that is the honest picture.
- If a service is marked new, say so plainly: it has too few orders to be sure yet. \
Never present a new service as proven.
- If a lookup comes back with available = false or a warning, tell the customer straight \
away that orders on it are getting stuck right now, and offer a better one from the same \
category. Never talk it up.
- For multiple options, a short bullet list is great. Bold the service name and key \
numbers. Never paste raw JSON or field names.
- Any price you show is approximate — say the exact price shows at checkout.

# Privacy & scope
- Never ask for, reveal, or look up usernames, emails, payments, order IDs or anyone's \
personal data. If someone asks about an account or an order, politely decline and \
redirect — you genuinely cannot see any of it.
- Your scope is services, their performance, pricing, and how the panel works. Anything \
about an account, a payment or a specific order → {config.PANEL_DOMAIN} or a support \
ticket from their dashboard.

# About the panel
{config.CHATBOT_SUPPORT_INFO}

Currency for prices: {config.CURRENCY}. Every number you get comes from real orders on \
this panel over the last {config.ROLLING_WINDOW_DAYS} days, with recent orders weighted \
more heavily than old ones."""


# ============================================================================
#  Gemini REST — tool loop
# ============================================================================
def _gemini_request(payload):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    try:
        resp = _SESSION.post(url, params={"key": config.GEMINI_API_KEY},
                             json=payload, timeout=45)
    except requests.RequestException as exc:
        raise GeminiError(f"network: {exc}") from exc
    if resp.status_code != 200:
        detail = ""
        try:
            detail = (resp.json().get("error") or {}).get("message", "")
        except ValueError:
            detail = resp.text[:200]
        raise GeminiError(f"HTTP {resp.status_code}: {detail}")
    return resp.json()


def _history_to_contents(history):
    contents = []
    for turn in history or []:
        role = "user" if turn.get("role") == "user" else "model"
        text = (turn.get("content") or "").strip()
        if not text:
            continue
        contents.append({"role": role, "parts": [{"text": text}]})
    # Gemini প্রথম টার্ন user চায় — শুরুর model টার্নগুলো বাদ
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


def _collect_text(parts):
    return "".join(p.get("text", "") for p in (parts or []) if p.get("text")).strip()


def _wrap_response(result):
    return result if isinstance(result, dict) else {"result": result}


def run_chat(history, lang=None):
    contents = _history_to_contents(history)
    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Hi"}]}]

    base = {
        "systemInstruction": {"parts": [{"text": build_system_prompt(lang)}]},
        "tools": TOOLS,
        "generationConfig": {"temperature": 0.75, "topP": 0.9, "maxOutputTokens": 900},
    }
    tools_used = []

    for _ in range(5):
        data = _gemini_request({**base, "contents": contents})
        candidates = data.get("candidates") or []
        if not candidates:
            break
        cand = candidates[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        calls = [p["functionCall"] for p in parts if p.get("functionCall")]

        if calls:
            contents.append({"role": "model", "parts": parts})
            resp_parts = []
            for call in calls:
                name = call.get("name")
                args = call.get("args") or {}
                tools_used.append(name)
                result = dispatch_tool(name, args)
                resp_parts.append({"functionResponse": {"name": name, "response": _wrap_response(result)}})
            contents.append({"role": _FUNC_ROLE, "parts": resp_parts})
            continue

        text = _collect_text(parts)
        if text:
            return {"reply": text, "tools_used": tools_used, "mode": "ai"}
        break

    # লুপ শেষেও টেক্সট নেই — শেষ চেষ্টা (টুল ছাড়া)
    try:
        data = _gemini_request({
            "systemInstruction": base["systemInstruction"],
            "contents": contents + [{"role": "user", "parts": [{"text":
                "Please give the customer a short, natural reply now, in their language."}]}],
            "generationConfig": base["generationConfig"],
        })
        text = _collect_text(((data.get("candidates") or [{}])[0].get("content") or {}).get("parts"))
        if text:
            return {"reply": text, "tools_used": tools_used, "mode": "ai"}
    except GeminiError:
        pass

    raise GeminiError("no text in response")


# ============================================================================
#  Fallback (নিয়ম-ভিত্তিক) — GEMINI_API_KEY না থাকলে বা Gemini ব্যর্থ হলে
# ============================================================================
def _greeting_text():
    return (f"Hi! 👋 I'm {config.BOT_NAME} from {config.PANEL_NAME}. I can recommend the "
            "best-performing service, look up any service by its ID, or estimate a cost. "
            "What do you need?")


def _no_orders_text():
    return (f"I can't see orders or accounts, so I can't check that one — sorry! "
            f"You'll find it under **My Orders** at {config.PANEL_DOMAIN}, and support "
            "can dig deeper from a ticket.\n\n"
            "What I *can* do: find the best service for a platform, pull up any "
            "service by its ID, or estimate a cost. Want any of those?")


def _help_text():
    return (f"Here's the quick version 👇\n\n"
            f"1. Sign in at **{config.PANEL_DOMAIN}** and add funds\n"
            "2. Pick a service and paste your link/username\n"
            "3. Set the quantity and hit **Submit**\n\n"
            "Want me to suggest the best service for a platform, or estimate a cost?")


def _default_text():
    return ("I can help with three things: finding the **best service** for a platform, "
            "**service details** if you have the ID, or an **order cost** estimate. "
            "Which one can I help with?")


def _fmt_services_md(result, header):
    if not result.get("results"):
        return result.get("message") or "I couldn't find a matching service right now."
    lines = [header, ""]
    for i, s in enumerate(result["results"][:5], 1):
        bits = []
        if s.get("completion_rate_percent") is not None:
            bits.append(f"{s['completion_rate_percent']}% delivered")
        if s.get("typical_delivery") and s["typical_delivery"] != "varies":
            bits.append(f"~{s['typical_delivery']}")
        lines.append(f"**{i}. {s['label']}**")
        if bits:
            lines.append("   " + " · ".join(bits))
    lines.append("")
    lines.append("Want a cost estimate for any of these?")
    return "\n".join(lines)


def _fmt_service_md(s):
    """সার্ভিসের তথ্য — কাস্টমারের পড়ার মতো করে।"""
    lines = [f"**{s['label']}**", ""]

    bits = []
    if s.get("price_per_1000"):
        bits.append(f"{s['price_per_1000']} per 1000")
    if s.get("min_order") and s.get("max_order"):
        bits.append(f"min {s['min_order']:,} / max {s['max_order']:,}")
    if bits:
        lines.append("• " + " · ".join(bits))

    if s.get("typical_delivery") and s["typical_delivery"] != "varies":
        line = f"• Usually delivers in **{s['typical_delivery']}**"
        if s.get("p90_delivery"):
            line += f" · 9 out of 10 within {s['p90_delivery']}"
        lines.append(line)
    if s.get("rank_in_category") and s.get("total_in_category", 0) > 1:
        lines.append(f"• Ranked **#{s['rank_in_category']} of {s['total_in_category']}** "
                     f"in {s.get('platform','')} {s.get('category','')}")
    if s.get("is_new"):
        lines.append("• 🆕 New service — not enough orders yet to be sure")
    if s.get("completion_rate_percent") is not None:
        lines.append(f"• **{s['completion_rate_percent']}%** of orders delivered in full"
                     + (f" (from {s['based_on_orders']:,} real orders)"
                        if s.get("based_on_orders") else ""))

    rec = s.get("recent_deliveries") or []
    if rec:
        lines.append("")
        lines.append("Recent deliveries: " +
                     " · ".join(f"{r['quantity']:,} in {r['completed_in']}" for r in rec[:3]))

    lines.append("")
    if s.get("available") is False:
        lines.append("⚠️ Heads up: orders on this one are currently getting stuck. "
                     "I'd pick a different service in the same category — want me to "
                     "find one?")
    else:
        lines.append("Want a cost estimate for a specific quantity?")
    return "\n".join(lines)


def _fmt_cost_md(res):
    if not res.get("ok"):
        return res.get("message") or "I couldn't estimate that cost."
    name = f" for **{res['service_name']}**" if res.get("service_name") else ""
    return (f"About **{res['estimated_cost']}** for {res['quantity']:,} units{name} "
            f"(≈ {res['price_per_1000']}/1k).\n_{res['note']}_")


def fallback_chat(history):
    msg = ""
    for turn in reversed(history or []):
        if turn.get("role") == "user":
            msg = (turn.get("content") or "").strip()
            break
    low = msg.lower()
    num = re.search(r"#?(\d{2,})", low)

    # --- অর্ডার সংক্রান্ত প্রশ্ন — সৎভাবে না বলা ---
    # এই বট অর্ডার দেখতে পায় না, তাই আন্দাজে উত্তর না দিয়ে সরাসরি জানিয়ে দেয়।
    order_words = ("track", "my order", "amar order", "order status", "order id",
                   "kotodur", "koto dur", "order kobe", "delivery status",
                   "refund", "refill", "cancel my")
    if any(k in low for k in order_words):
        return {"reply": _no_orders_text(), "tools_used": [], "mode": "fallback"}

    # --- সার্ভিস আইডি → সার্ভিসের তথ্য ---
    # এই বট অর্ডার ট্র্যাক করে না। যেকোনো সংখ্যা সার্ভিস আইডি হিসেবেই দেখা হয়।
    if num:
        info = get_service_info(num.group(1))
        if info.get("ok"):
            return {"reply": _fmt_service_md(info),
                    "tools_used": ["get_service_info"], "mode": "fallback"}
        if len(low) <= 12:
            return {"reply": f"I couldn't find a service with ID {num.group(1)}. "
                             "Tell me what you're after — platform and type — and "
                             "I'll find the right one for you.",
                    "tools_used": ["get_service_info"], "mode": "fallback"}

    # --- দাম / খরচ ---
    if any(k in low for k in ("price", "cost", "rate", "charge", "koto", "dam", "how much", "kto")):
        if num and any(k in low for k in ("how much", "cost", "koto", "dam", "charge", "kto")):
            qty = int(num.group(1))
            res = calculate_order_cost(qty, service_query=low)
            return {"reply": _fmt_cost_md(res),
                    "tools_used": ["search_services", "calculate_order_cost"], "mode": "fallback"}
        res = search_services(query=low, platform=_detect_platform(low), sort_by="price_low", limit=5)
        return {"reply": _fmt_services_md(res, "Here are some options and their typical rates:"),
                "tools_used": ["search_services"], "mode": "fallback"}

    # --- বেস্ট / রেকমেন্ডেশন ---
    if any(k in low for k in ("best", "recommend", "suggest", "top", "valo", "bhalo", "sera",
                              "which service", "kon service", "good service", "vhalo")):
        res = search_services(query=low, platform=_detect_platform(low), sort_by="quality", limit=5)
        return {"reply": _fmt_services_md(res, "Based on real order performance, these are doing best right now:"),
                "tools_used": ["search_services"], "mode": "fallback"}

    # --- কীভাবে অর্ডার / সাহায্য ---
    if any(k in low for k in ("how ", "kivabe", "kibhabe", "ki vabe", "order dibo", "order koro", "start", "guide")):
        return {"reply": _help_text(), "tools_used": [], "mode": "fallback"}

    # --- অভিবাদন / ডিফল্ট ---
    greet = ("hi", "hello", "hey", "assalam", "salam", "hlw", "nomoskar", "ki khobor",
             "good morning", "good evening", "good afternoon")
    if len(low) < 3 or any(low.startswith(g) or g in low for g in greet):
        return {"reply": _greeting_text(), "tools_used": [], "mode": "fallback"}

    return {"reply": _default_text(), "tools_used": [], "mode": "fallback"}


# ============================================================================
#  পাবলিক এন্ট্রি পয়েন্ট
# ============================================================================
def chat(history, lang=None):
    """history: [{'role': 'user'|'model'|'assistant', 'content': str}, ...] (শেষটি বর্তমান মেসেজ)।
    রিটার্ন: {'reply': str, 'tools_used': [str], 'mode': 'ai'|'fallback'|'fallback-error'}
    """
    if config.GEMINI_API_KEY:
        try:
            return run_chat(history, lang)
        except Exception as exc:  # noqa: BLE001 — যেকোনো সমস্যায় fallback
            print(f"[chatbot] Gemini error → fallback: {exc}")
            res = fallback_chat(history)
            res["mode"] = "fallback-error"
            return res
    return fallback_chat(history)
