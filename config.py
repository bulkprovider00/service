"""কনফিগারেশন — .env ফাইল ও এনভায়রনমেন্ট ভেরিয়েবল থেকে সেটিংস নেয়"""
import os
import sys
from pathlib import Path

# Windows কনসোলের ডিফল্ট এনকোডিং (cp1252) বাংলা টেক্সট প্রিন্টে UnicodeEncodeError দেয়।
# সব মডিউল config ইম্পোর্ট করে, তাই এখানে একবার stdout/stderr UTF-8 করলেই যথেষ্ট।
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# --- Mothersmm Admin API v2 ---
# পুরনো MOTHERSMM_* নামও পড়া হয়, যাতে চালু ডিপ্লয় হঠাৎ ভেঙে না যায়।
API_KEY = (os.environ.get("SMM_ADMIN_API_KEY")
           or os.environ.get("MOTHERSMM_API_KEY", "")).strip()
API_BASE = (os.environ.get("SMM_ADMIN_API_BASE")
            or os.environ.get("MOTHERSMM_API_BASE")
            or "https://bulkprovider.com/adminapi/v2").rstrip("/")

# --- রিসেলার API v2 (সার্ভিস ক্যাটালগ / কোনটা এখনো চালু আছে) ---
# Admin API-তে সার্ভিস লিস্ট নেই। নিজের প্যানেলে একটা অ্যাকাউন্টের API key নিন
# (Account → API) এবং এখানে বসান। না দিলে সব কিছু চলবে, শুধু ডিজেবল সার্ভিস
# শনাক্ত করা যাবে না।
USER_API_KEY = os.environ.get("SMM_USER_API_KEY", "").strip()
PUBLIC_API_URL = os.environ.get(
    "SMM_PUBLIC_API_URL",
    "https://" + os.environ.get("CHATBOT_PANEL_DOMAIN", "bulkprovider.com").strip() + "/api/v2",
).strip()

# --- ডেটা উইন্ডো ও শিডিউল ---
ROLLING_WINDOW_DAYS = int(os.environ.get("SMM_WINDOW_DAYS", "120"))     # রোলিং উইন্ডো (দিন)
DAILY_SYNC_HOUR = int(os.environ.get("SMM_SYNC_HOUR", "0"))             # প্রতিদিন কয়টায় আপডেট (সার্ভার লোকাল সময়)
DAILY_SYNC_MINUTE = int(os.environ.get("SMM_SYNC_MINUTE", "0"))

# --- ডাটাবেজ (আলাদা ডেডিকেটেড ডাটাবেজ) ---
DB_PATH = os.environ.get("SMM_DB_PATH", str(BASE_DIR / "data" / "smm_orders.db"))

# --- র‍্যাংকিং ---
MIN_ORDERS_FOR_RANK = int(os.environ.get("SMM_MIN_ORDERS", "10"))       # এর চেয়ে কম অর্ডার হলে "best" র‍্যাংকিংয়ে ধরা হবে না

# ============================================================================
#  স্কোরিং
# ============================================================================
# ওজন (মোট = 1.0)। শুধু নিষ্পত্তি হওয়া (settled) অর্ডারের উপর হিসাব —
# চলমান অর্ডার ব্যর্থতা হিসেবে গোনা হয় না।
W_COMPLETION = float(os.environ.get("SMM_W_COMPLETION", "0.55"))  # পুরো ডেলিভারি
W_ISSUES     = float(os.environ.get("SMM_W_ISSUES",     "0.10"))  # cancel + partial একসাথে
W_STUCK      = float(os.environ.get("SMM_W_STUCK",      "0.20"))  # আটকে থাকা অর্ডার
W_VOLUME     = float(os.environ.get("SMM_W_VOLUME",     "0.05"))  # কত অর্ডারের ভিত্তিতে
W_SPEED      = float(os.environ.get("SMM_W_SPEED",      "0.10"))  # গতি

# আটকা হার → গুণক। ওজন প্রতিটা পয়েন্ট মাপে, গুণক সীমা ছাড়ালে লাইন কেটে দেয়।
STUCK_MULTIPLIERS = [(0.00, 1.00), (0.02, 0.90), (0.05, 0.70),
                     (0.10, 0.45), (0.20, 0.25)]
STUCK_MULTIPLIER_WORST = 0.12          # এর বাইরে হলে

# কোন স্ট্যাটাস কত ঘণ্টা পর "আটকা"।
# এই প্যানেলে স্বাভাবিক অর্ডার pending এ যায়; সমস্যা হলে processing এ যায় —
# তাই processing সাথে সাথেই আটকা ধরা হয়।
STUCK_PROCESSING_HOURS   = float(os.environ.get("SMM_STUCK_PROCESSING_HOURS", "0"))
STUCK_PENDING_HOURS      = float(os.environ.get("SMM_STUCK_PENDING_HOURS", "24"))
STUCK_IN_PROGRESS_HOURS  = float(os.environ.get("SMM_STUCK_IN_PROGRESS_HOURS", "24"))

# সাম্প্রতিকতা: পুরনো অর্ডারের ওজন কত দিনে অর্ধেক হবে
DECAY_HALFLIFE_DAYS = float(os.environ.get("SMM_DECAY_HALFLIFE_DAYS", "14"))

# নতুন/কম-অর্ডারের সার্ভিসকে ক্যাটাগরির গড়ের দিকে টেনে রাখার জোর।
# K = যত অর্ডারে সার্ভিসের নিজের ডেটা অর্ধেক ওজন পায়।
PRIOR_K = float(os.environ.get("SMM_PRIOR_K", "10"))

# "NEW" = সত্যিই নতুন সার্ভিস — মোট অর্ডার এর কম (Orders কলামের সাথেই মেলে)
NEW_SERVICE_MAX_ORDERS = float(os.environ.get("SMM_NEW_MAX_ORDERS", "10"))

# "THIN" = পুরনো সার্ভিস, কিন্তু সাম্প্রতিক অর্ডার নেই বলে প্রমাণ কম।
# ক্ষয়-ওজন দেওয়া কার্যকর অর্ডার এর কম হলে।
THIN_EVIDENCE_MAX = float(os.environ.get("SMM_THIN_MAX_ORDERS", "3"))

# গতি মাপার সাইজ-বাকেট — একই আকারের অর্ডারের সাথেই তুলনা হয়
SIZE_BUCKETS = [(0, 1000, "<1k"), (1000, 10000, "1k-10k"),
                (10000, 100000, "10k-100k"), (100000, None, "100k+")]

# Trend: শেষ কত দিন "এখন", তার আগের কত দিন "আগে"
TREND_RECENT_DAYS = int(os.environ.get("SMM_TREND_RECENT_DAYS", "14"))
TREND_PRIOR_DAYS  = int(os.environ.get("SMM_TREND_PRIOR_DAYS", "45"))
TREND_THRESHOLD   = float(os.environ.get("SMM_TREND_THRESHOLD", "5"))  # পয়েন্ট
# দুই জানালাতেই অন্তত এতগুলো নিষ্পত্তি অর্ডার না থাকলে trend দেখানো হয় না।
# নইলে ১-২টা অর্ডারেই ±৬৬% এর মতো অর্থহীন লাফ আসে।
TREND_MIN_ORDERS  = int(os.environ.get("SMM_TREND_MIN_ORDERS", "5"))

# --- প্রাইভেসি ---
# রেভিনিউ কখনো ডাটাবেজে রাখা হয় না। True করলে শুধু প্রতি-১০০০ ইউনিট দাম
# রাখা হবে (ক্যাটালগের তথ্য, আয় নয়) — মোট চার্জ কখনোই নয়।
# প্রতি ১০০০ ইউনিটের দাম রাখা হয় (মোট চার্জ কখনো নয়)।
# এই প্যানেলে দাম ফিক্সড, কাস্টমারভেদে ডিসকাউন্ট নেই — তাই দেখানো নিরাপদ।
STORE_UNIT_PRICE = os.environ.get("SMM_STORE_UNIT_PRICE", "true").lower() == "true"


# ============================================================================
#  🤖 AI চ্যাটবট (Gemini) কনফিগ
# ============================================================================
# Gemini API key — https://aistudio.google.com/apikey থেকে ফ্রি নিন।
# key না থাকলেও উইজেট চলবে (নিয়ম-ভিত্তিক ফলব্যাক মোডে), তবে আসল human-like
# উত্তরের জন্য key দিন।
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

# --- বটের পরিচয় ও ব্র্যান্ডিং (উইজেটে দেখাবে) ---
# বটের নাম — .env এ CHATBOT_NAME দিয়ে বসান
BOT_NAME = os.environ.get("CHATBOT_NAME", "Assistant").strip()
PANEL_NAME = os.environ.get("CHATBOT_PANEL_NAME", "BulkProvider").strip()
PANEL_DOMAIN = os.environ.get("CHATBOT_PANEL_DOMAIN", "bulkprovider.com").strip()
BRAND_COLOR = os.environ.get("CHATBOT_BRAND_COLOR", "#6c5ce7").strip()
CURRENCY = os.environ.get("SMM_CURRENCY", "USD").strip().upper()

# শুরুর অভিবাদন (বাবল) — ব্যবহারকারীর ভাষায় বটই আবার মানিয়ে নেবে
CHATBOT_GREETING = os.environ.get(
    "CHATBOT_GREETING",
    f"Hi! 👋 I'm {BOT_NAME} from {PANEL_NAME}. Ask me for the best service, "
    "pricing, or to track an order — how can I help?",
).strip()
CHATBOT_GREETING_INTERVAL_HOURS = int(os.environ.get("CHATBOT_GREETING_INTERVAL_HOURS", "6"))

# শুরুর সাজেশন চিপ (ব্যবহারকারী ক্লিক করে দ্রুত জিজ্ঞেস করতে পারে)
CHATBOT_SUGGESTIONS = [
    "🏆 Best Instagram service?",
    "💰 Pricing for 1000 followers",
    "🔎 Details for service ID",
    "❓ How do I place an order?",
]

# কথোপকথনের কয়টি সাম্প্রতিক টার্ন AI-কে দেওয়া হবে (কনটেক্সটের জন্য)
CHATBOT_HISTORY_TURNS = int(os.environ.get("CHATBOT_HISTORY_TURNS", "16"))

# রেট লিমিট — একটি IP থেকে প্রতি উইন্ডোতে সর্বোচ্চ কয়টি মেসেজ (Gemini কোটা রক্ষায়)
CHATBOT_RATE_LIMIT = int(os.environ.get("CHATBOT_RATE_LIMIT", "20"))
CHATBOT_RATE_WINDOW_SEC = int(os.environ.get("CHATBOT_RATE_WINDOW_SEC", "60"))

# প্যানেল সম্পর্কিত FAQ/সাপোর্ট তথ্য — system prompt-এ যুক্ত হয়।
# এখানে আপনার প্যানেলের আসল নিয়মকানুন লিখে দিন (পেমেন্ট, রিফিল, ডেলিভারি ইত্যাদি)।
CHATBOT_SUPPORT_INFO = os.environ.get("CHATBOT_SUPPORT_INFO", "").strip() or f"""\
- {PANEL_NAME} is an SMM (social media marketing) panel at {PANEL_DOMAIN} offering \
followers, likes, views, comments, subscribers and more across Instagram, Facebook, \
TikTok, YouTube, Telegram, Twitter/X and other platforms.
- To place an order: sign in at {PANEL_DOMAIN}, add funds to your balance, pick a \
service, paste your link/username, set the quantity, and click Submit.
- Payments: add funds from the "Add Funds" page using the methods shown there.
- Delivery: start time and speed vary by service — check the service description and \
the quality/speed stats I can look up for you.
- Refill/refund: covered by each service's own policy shown on the order page; for \
disputes, open a support ticket from your dashboard.
- I can recommend the best-performing service for a platform, look up any service by \
its ID, and estimate the cost of an order.
- I cannot see anyone's account or orders. For order status, sign in at {PANEL_DOMAIN} \
and open My Orders; for anything else, open a support ticket from the dashboard."""

