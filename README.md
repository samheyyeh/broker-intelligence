# Insurance Broker Intelligence — Backend

## Stack
- **Python Flask** backend (`app.py`) — serves `/api/news`
- **Free RSS feeds** — Google News + Yahoo Finance (no API key needed)
- **Claude API** — used for news analysis/scoring (requires ANTHROPIC_API_KEY)
- **Vanilla JS frontend** (`index.html`) — calls the Flask backend

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Start the Flask backend
python app.py
# → Runs on http://localhost:5050

# 4. Open index.html in your browser
#    (or serve it with: python -m http.server 8080)
```

## Architecture

```
index.html  ──fetch──▶  Flask :5050  ──RSS──▶  Google News / Yahoo Finance
                              │
                              └──Claude API──▶  Impact scoring & analysis
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/news` | Fetch + analyze latest broker news |
| `GET /api/news?q=query` | Filter by search term |
| `GET /api/health` | Health check |

## Free Data Sources Used

- **Google News RSS** — searches for each broker by name/ticker (no key)
- **Yahoo Finance RSS** — per-ticker headline feeds (no key)
- **Claude API** — for analysis, scoring, risk/opportunity extraction

## Caching

Results are cached in-memory for **5 minutes** to avoid hammering RSS feeds 
and Claude API on every page load. Adjust `CACHE_TTL` in `app.py`.
