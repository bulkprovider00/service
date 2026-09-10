"""সার্ভিসের নাম/টাইপ দেখে প্ল্যাটফর্ম ও ক্যাটাগরি (service type) শনাক্ত করা"""
import re

# প্ল্যাটফর্ম শনাক্তকরণ — (প্ল্যাটফর্ম, কীওয়ার্ড লিস্ট)। ম্যাচ হয় পূর্ণ-শব্দ হিসেবে।
PLATFORMS = {
    "Instagram":    ["instagram", "insta", "ig", "igtv"],
    "TikTok":       ["tiktok", "tik tok", "douyin"],
    "YouTube":      ["youtube", "yt", "you tube"],
    "Facebook":     ["facebook", "fb", "fbook", "meta"],
    "Telegram":     ["telegram", "tg"],
    "Twitter":      ["twitter", "tweet", "tweets"],
    "WhatsApp":     ["whatsapp", "whats app"],
    "Threads":      ["threads"],
    "LinkedIn":     ["linkedin", "linked in"],
    "Spotify":      ["spotify"],
    "Snapchat":     ["snapchat", "snap"],
    "Discord":      ["discord"],
    "Twitch":       ["twitch"],
    "Reddit":       ["reddit"],
    "Pinterest":    ["pinterest"],
    "Kick":         ["kick"],
    "SoundCloud":   ["soundcloud", "sound cloud"],
    "Bluesky":      ["bluesky", "blue sky", "bsky"],
    "Tumblr":       ["tumblr"],
    "Quora":        ["quora"],
    "Medium":       ["medium"],
    "Vimeo":        ["vimeo"],
    "VKontakte":    ["vkontakte", "vk"],
    "Website":      ["website", "web traffic", "site traffic"],
    "GoogleMaps":   ["google maps", "googlemaps", "gmb", "google business", "google"],
    "Kwai":         ["kwai"],
    "Likee":        ["likee"],
    "Lemon8":       ["lemon8", "lemon 8"],
    "SnackVideo":   ["snackvideo", "snack video"],
    "LINE":         ["line app", "line official"],
    "Clubhouse":    ["clubhouse", "club house"],
    "Steam":        ["steam"],
    "Github":       ["github", "git hub"],
    "Mastodon":     ["mastodon"],
    "Apple":        ["apple music", "itunes", "app store", "apple"],
    "Android":      ["google play", "play store", "android"],
    "Deezer":       ["deezer"],
    "Shazam":       ["shazam"],
    "Tidal":        ["tidal"],
    "Audiomack":    ["audiomack", "audio mack"],
    "MixCloud":     ["mixcloud", "mix cloud"],
    "Dailymotion":  ["dailymotion", "daily motion"],
    "Rumble":       ["rumble"],
    "Odnoklassniki":["odnoklassniki", "ok ru"],
    "Dribbble":     ["dribbble"],
    "Behance":      ["behance"],
    "Crypto":       ["crypto", "coinmarketcap", "coingecko"],
    "Rutube":       ["rutube"],
    "Spinnin":      ["spinnin"],
    "2GIS":         ["2gis", "2 gis"],
    "Shopee":       ["shopee"],
    "Reverbnation": ["reverbnation", "reverb nation"],
    "Dzen":         ["dzen", "yandex zen"],
    "Datpiff":      ["datpiff"],
    "Trovo":        ["trovo"],
    "YandexMaps":   ["yandex maps", "yandexmaps"],
    "Yandex":       ["yandex"],
    "Fanvue":       ["fanvue"],
    "Boomplay":     ["boomplay", "boom play"],
    "500px":        ["500px", "500 px"],
    "Triller":      ["triller"],
    "Fancentro":    ["fancentro", "fan centro"],
    "Dlive":        ["dlive", "d live"],
    "Shedevrum":    ["shedevrum"],
    "Menti":        ["menti", "mentimeter"],
    "Vc.ru":        ["vc ru", "vcru"],
    "Polltab":      ["polltab", "poll tab"],
    "Strawpoll":    ["strawpoll", "straw poll"],
    "Bigo":         ["bigo"],
    "Coub":         ["coub"],
    "Aparat":       ["aparat"],
    "Kuaishou":     ["kuaishou", "kuai shou"],
    "Avito":        ["avito"],
    "MegaMarket":   ["megamarket", "mega market"],
    "Ozon":         ["ozon"],
    "Wildberries":  ["wildberries", "wild berries"],
    "Jaco":         ["jaco"],
    "MAX.RU":       ["max ru", "maxru"],
    "Videa":        ["videa"],
    "Odysee":       ["odysee"],
    "Bitchute":     ["bitchute", "bit chute"],
    "Wibes":        ["wibes"],
    "Mail":         ["mail ru", "mailru"],
    "Lazada":       ["lazada"],
}

# ক্যাটাগরি শনাক্তকরণ — স্পেসিফিক টার্ম আগে রাখা হয়েছে যাতে ভুল ম্যাচ না হয়
CATEGORIES = [
    ("Live Views",      ["live view", "live stream", "livestream", "live viewer"]),
    ("Story Views",     ["story view", "stories view", "story"]),
    ("Reels Views",     ["reels view", "reel view", "reels play"]),
    ("Watch Time",      ["watch time", "watchtime", "watch hour"]),
    ("Premiere Views",  ["premiere view", "premiere"]),
    ("Reviews",         ["review", "rating", "star rating"]),
    ("Subscribers",     ["subscriber", "subs", "sub"]),
    ("Followers",       ["follower", "follow", "page like", "page follow"]),
    ("Members",         ["member", "group join", "channel member"]),
    ("Friends",         ["friend request", "friend"]),
    ("Connections",     ["connection"]),
    ("Comment Likes",   ["comment like"]),
    ("Comments",        ["comment", "reply"]),
    ("Reactions",       ["reaction", "react", "emoji"]),
    ("Dislikes",        ["dislike", "downvote"]),
    ("Likes",           ["like", "favorite", "favourite", "heart", "upvote"]),
    ("Shares",          ["share", "retweet", "repost", "reblog"]),
    ("Saves",           ["save", "bookmark", "collect"]),
    ("Plays",           ["play", "stream", "listen"]),
    ("Views",           ["view", "impression", "reach"]),
    ("Clicks",          ["click", "link click", "ctr"]),
    ("Traffic",         ["traffic", "visitor", "session"]),
    ("Votes",           ["vote", "poll", "contest"]),
    ("Downloads",       ["download", "install"]),
    ("Mentions",        ["mention", "tag"]),
    ("Live Gifts",      ["gift", "coin", "diamond"]),
    ("Reports",         ["report"]),
    ("Verification",    ["verification", "verified", "blue tick", "blue badge"]),
    ("Accounts",        ["account"]),
    ("Engagement",      ["engagement", "interaction"]),
]


def detect_platform_category(service_name: str, service_type: str = "") -> tuple[str, str]:
    """(platform, category) রিটার্ন করে; না চিনলে 'Other'।

    প্ল্যাটফর্ম ম্যাচিং হয় পূর্ণ-শব্দ (word boundary) ভিত্তিতে — যাতে ছোট কীওয়ার্ড
    (যেমন 'insta') বড় শব্দের ভেতরে (যেমন 'instant') ভুলভাবে ম্যাচ না করে।
    """
    text = f"{service_name or ''} {service_type or ''}".lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    def has_word(kw: str) -> bool:
        kw = kw.strip()
        return bool(kw) and re.search(rf"\b{re.escape(kw)}\b", text) is not None

    platform = "Other"
    for name, keywords in PLATFORMS.items():
        if any(has_word(kw) for kw in keywords):
            platform = name
            break

    # ক্যাটাগরিতে সাবস্ট্রিং ম্যাচ (একবচন/বহুবচন — like/likes, view/views — কভার করতে)
    category = "Other"
    for name, keywords in CATEGORIES:
        if any(kw.strip() in text for kw in keywords):
            category = name
            break

    return platform, category
