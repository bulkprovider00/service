# BulkProvider — Service Performance & AI Support

Ranks every service from real order history, and answers customer questions
from that same data. Nothing is invented.

---

## What's in here

```
api.py                 FastAPI server — all routes, startup, scheduler
statistics_engine.py   Scoring, ranking, trend
collector.py           Admin API → database
database.py            SQLite schema + migrations
platform_mapper.py     Service name → (platform, category)
orders_feed.py         Public completed-order feed
chatbot.py             AI support agent (Gemini + 3 tools)
scheduler.py           Daily auto-sync
config.py              All settings
dashboard.html         Admin dashboard  (served at /)
widget.js              Embeddable chat widget
smm-stats.js           Embeddable completed-order feed widget
chatbot_demo.html      Chat demo page  (served at /chat)

panel-pages/
  statistics-page.txt  Panel Statistics page  (paste into PerfectPanel)
  neworder-page.txt    Panel New Order page   (paste into PerfectPanel)
```

---

## Setup

```bash
cp .env.example .env          # fill in SMM_ADMIN_API_KEY
pip install -r requirements.txt
python api.py                 # → http://localhost:8000
```

### Required

| Variable | What it is |
| --- | --- |
| `SMM_ADMIN_API_KEY` | Admin API key from your panel |
| `SMM_ADMIN_API_BASE` | Where order data comes from, e.g. `https://yourpanel.com/adminapi/v2` |

### Worth setting

| Variable | Default | What it does |
| --- | --- | --- |
| `CHATBOT_NAME` | `Assistant` | The bot's name |
| `GEMINI_API_KEY` | *(empty)* | Without it the bot still works, on rule-based fallback |
| `CHATBOT_PANEL_NAME` | `BulkProvider` | Shown to customers |
| `CHATBOT_PANEL_DOMAIN` | `bulkprovider.com` | Shown to customers |
| `SMM_CURRENCY` | `USD` | Price display |

---

## Scoring

Only **settled** orders count — orders still running are not treated as failures.

```
55%  completion    completed / settled
10%  issues        (canceled + partial) / settled
20%  stuck         orders sitting without delivering
 5%  volume        how much data this is based on
10%  speed         vs other services in the same category, at the same order size

× stuck multiplier   0% → ×1.00 ... over 20% → ×0.12
× category prior     (n × own + 10 × category average) / (n + 10)
```

**Stuck** means: `processing` immediately (on this panel that status signals a
problem, not a normal step), `pending` and `in_progress` after 24 hours.

**Recency**: every order is weighted `0.5 ^ (age_days / 14)`. Old orders are
never dropped, just counted less — so a service that went bad last week shows
it, while a low-volume service doesn't run out of data.

**New services** start at their category average instead of zero, so they can
be found at all, and carry a `NEW` label until they have enough orders.

Tune any of it in `config.py` or via environment variables.

---

## API

| Endpoint | For |
| --- | --- |
| `GET /` | Admin dashboard |
| `GET /docs` | Swagger UI |
| `GET /api/status` | System status, last sync |
| `GET /api/statistics/ai` | Everything in one call (for the AI) |
| `GET /api/statistics/services` | Full stats, all services |
| `GET /api/statistics/services/{id}` | One service |
| `GET /api/statistics/{platform}/best-services` | Best per category |
| `GET /api/statistics/{platform}/{category}` | Full ranking in a category |
| `GET /api/services/public` | Customer-safe list |
| `GET /api/orders/completed` | Live completed-order feed |
| `GET /api/orders/completed/{id}` | Recent delivery times for one service |
| `POST /api/sync` | Manual sync |
| `GET /widget.js`, `/smm-stats.js` | Embeddable widgets |

Revenue is never stored and never returned. Order totals are dropped at
collection time; set `SMM_STORE_UNIT_PRICE=true` to keep only the per-1000
price, which is catalog information customers already see.

---

## Embedding on the panel

Statistics page:

```html
<div data-smm-feed data-limit="100" data-max="1000"></div>
<script src="https://YOUR-HOST/smm-stats.js" data-api="https://YOUR-HOST" async></script>
```

Chat widget, any page:

```html
<script src="https://YOUR-HOST/widget.js" async></script>
```

The two files in `panel-pages/` are complete PerfectPanel templates — paste them
in and set the API host near the top of each.

---

## Deploying on Render

`render.yaml` is a Blueprint. Set `SMM_ADMIN_API_KEY`, `SMM_ADMIN_API_BASE`,
`GEMINI_API_KEY` and `CHATBOT_NAME` in the dashboard.

On the free plan the service sleeps and has no persistent disk, so the database
is rebuilt on each deploy. A cron ping to `/api/status` keeps it awake and
triggers a sync when the data is stale.
