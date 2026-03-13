"""
Insurance Broker Intelligence - Python Backend
Uses free RSS feeds + Claude API for analysis (no paid news APIs)
"""

import json
import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PASSPHRASE = os.environ.get("PASSPHRASE", "")
# Simple in-memory valid token store (resets on redeploy, which is fine)
_valid_tokens: set = set()

# ─── Broker Config ─────────────────────────────────────────────────────────────

BROKERS = {
    "AJG": "Arthur J. Gallagher",
    "AON": "Aon",
    "MMC": "Marsh McLennan",
    "WTW": "Willis Towers Watson",
    "BRO": "Brown & Brown",
    "HIG": "Hartford Financial",
}

BROKER_KEYWORDS = {
    "AJG": ["gallagher", "ajg", "arthur j. gallagher"],
    "AON": ["aon"],
    "MMC": ["marsh", "mclennan", "mmc", "marsh & mclennan"],
    "WTW": ["wtw", "willis towers watson", "willis"],
    "BRO": ["brown & brown", "bro insurance"],
    "HIG": ["hartford", "the hartford", "hig"],
}

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=insurance+broker+AJG+Gallagher&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Aon+insurance+broker&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Marsh+McLennan+insurance&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Willis+Towers+Watson+WTW&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Brown+Brown+insurance+broker&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Hartford+Financial+insurance&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=commercial+insurance+broker+acquisition&hl=en-US&gl=US&ceid=US:en",
    "https://finance.yahoo.com/rss/headline?s=AJG",
    "https://finance.yahoo.com/rss/headline?s=AON",
    "https://finance.yahoo.com/rss/headline?s=MMC",
    "https://finance.yahoo.com/rss/headline?s=WTW",
    "https://finance.yahoo.com/rss/headline?s=BRO",
    "https://finance.yahoo.com/rss/headline?s=HIG",
]

CATEGORY_KEYWORDS = {
    "M&A": ["acqui", "merger", "deal", "buyout", "purchase", "takeover", "combine"],
    "Regulatory": ["regulat", "sec ", "fca ", "doj ", "compliance", "fine", "penalty", "investigation", "probe"],
    "Earnings": ["earnings", "revenue", "profit", "quarterly", "q1", "q2", "q3", "q4", "financial results", "eps", "guidance"],
    "Leadership": ["ceo", "cfo", "appoint", "resign", "executive", "president", "hire", "depart", "chief"],
    "Technology": ["ai ", "artificial intelligence", "digital", "technology", "platform", "software", "cyber", "insurtech"],
    "Litigation": ["lawsuit", "sue", "litigation", "settlement", "court", "legal", "claim", "verdict"],
    "Market": ["market", "growth", "rate", "premium", "capacity", "loss ratio", "catastrophe", "nat cat", "hard market"],
}



def _clean_source(raw):
    """Clean up ugly Google News query strings as source names."""
    if not raw:
        return "News Feed"
    # Google News search feeds have titles like '"query term" - Google News'
    if raw.startswith('"') or "- Google News" in raw:
        return "Google News"
    return raw


def fetch_rss_entries(query=None):
    entries = []
    seen_titles = set()

    feeds_to_use = RSS_FEEDS
    if query:
        encoded = requests.utils.quote(query)
        extra = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        feeds_to_use = [extra] + RSS_FEEDS

    headers = {"User-Agent": "Mozilla/5.0 (compatible; InsuranceBotRSS/1.0)"}

    for url in feeds_to_use:
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            feed = feedparser.parse(resp.content)
            for e in feed.entries[:8]:
                title = e.get("title", "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                pub = None
                if hasattr(e, "published_parsed") and e.published_parsed:
                    pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat()

                summary = ""
                if hasattr(e, "summary"):
                    soup = BeautifulSoup(e.summary, "html.parser")
                    summary = soup.get_text(" ", strip=True)[:400]

                entries.append({
                    "title": title,
                    "summary": summary,
                    "link": e.get("link", ""),
                    "source": _clean_source(feed.feed.get("title", url.split("/")[2])),
                    "publishedAt": pub,
                })
        except Exception as ex:
            print(f"[RSS] Failed {url[:60]}: {ex}")

    return entries


def detect_brokers(text):
    lower = text.lower()
    return [bid for bid, kws in BROKER_KEYWORDS.items() if any(k in lower for k in kws)]


def detect_category(text):
    lower = text.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in lower for k in kws):
            return cat
    return "Market"


def make_id(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


ANALYSIS_SYSTEM = """You are a senior commercial insurance broker analyst.
Analyze news headlines about insurance brokers and return ONLY a raw JSON array.
No markdown, no backticks, no explanation — just the JSON array starting with [ and ending with ].

Each element must have these exact fields:
{
  "id": "<copy id from input exactly>",
  "headline": "<punchy headline under 100 chars>",
  "summary": "<2 sentences: what happened and why it matters>",
  "impactScore": <integer from -10 to 10, never 0 unless truly trivial>,
  "impactLabel": "<HIGH_POSITIVE|MODERATE_POSITIVE|NEUTRAL|MODERATE_NEGATIVE|HIGH_NEGATIVE>",
  "impactReasoning": "<1-2 sentences on strategic impact>",
  "keyRisks": ["<risk>", "<risk>"],
  "keyOpportunities": ["<opportunity>", "<opportunity>"],
  "urgency": "<BREAKING|TODAY|THIS_WEEK|MONITOR>"
}

Scoring:
- HIGH_POSITIVE 7-10: Major acquisition, blowout earnings, landmark deal
- MODERATE_POSITIVE 3-6: Growth news, good hire, product win
- NEUTRAL -2 to 2: Analyst reiterations, minor stock moves
- MODERATE_NEGATIVE -3 to -6: Earnings miss, exec departure, minor fine
- HIGH_NEGATIVE -7 to -10: Major lawsuit, regulatory action, catastrophic loss"""


def analyze_with_claude(raw_items):
    if not raw_items:
        return []

    if not ANTHROPIC_API_KEY:
        print("[Claude] ERROR: ANTHROPIC_API_KEY not set")
        raise ValueError("ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=sk-ant-...")

    payload = [
        {"id": item["id"], "title": item["title"], "summary": item["summary"][:300], "source": item["source"]}
        for item in raw_items
    ]

    print(f"[Claude] Analyzing {len(payload)} items...")

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4000,
            "system": ANALYSIS_SYSTEM,
            "messages": [{
                "role": "user",
                "content": f"Analyze these {len(payload)} news items. Return only the JSON array:\n\n{json.dumps(payload, indent=2)}"
            }]
        },
        timeout=60
    )

    if resp.status_code != 200:
        raise ValueError(f"Claude API HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if "error" in data:
        raise ValueError(f"Claude error: {data['error'].get('message', str(data['error']))}")

    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = re.sub(r"```json\s*|```\s*", "", text).strip()
    print(f"[Claude] Response: {len(text)} chars")

    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        print(f"[Claude] No JSON found in: {text[:300]}")
        raise ValueError("No JSON array returned by Claude")

    analyzed = json.loads(match.group())
    analyzed_map = {str(a["id"]): a for a in analyzed}

    result = []
    for item in raw_items:
        enriched = analyzed_map.get(str(item["id"]), {})
        result.append({
            "id": item["id"],
            "headline": enriched.get("headline", item["title"]),
            "summary": enriched.get("summary", item["summary"]),
            "source": item["source"],
            "publishedAt": item["publishedAt"],
            "link": item.get("link", ""),
            "category": item["category"],
            "affectedBrokers": item["affectedBrokers"],
            "impactScore": enriched.get("impactScore", 0),
            "impactLabel": enriched.get("impactLabel", "NEUTRAL"),
            "impactReasoning": enriched.get("impactReasoning", ""),
            "keyRisks": enriched.get("keyRisks", []),
            "keyOpportunities": enriched.get("keyOpportunities", []),
            "urgency": enriched.get("urgency", "MONITOR"),
        })
    return result


_cache = {"data": [], "ts": 0, "query": ""}
CACHE_TTL = 300


def get_news(query=""):
    now = time.time()
    if _cache["data"] and (now - _cache["ts"] < CACHE_TTL) and _cache["query"] == query:
        print("[Cache] Hit")
        return _cache["data"]

    print(f"[Fetch] RSS for query='{query}'")
    raw_entries = fetch_rss_entries(query or None)
    print(f"[Fetch] {len(raw_entries)} raw entries")

    enriched_raw = []
    for e in raw_entries:
        combined = f"{e['title']} {e['summary']}"
        brokers = detect_brokers(combined)
        if not brokers and not query:
            continue
        e["id"] = make_id(e["title"])
        e["affectedBrokers"] = brokers if brokers else []
        e["category"] = detect_category(combined)
        enriched_raw.append(e)

    seen = set()
    unique = []
    for e in enriched_raw:
        if e["id"] not in seen:
            seen.add(e["id"])
            unique.append(e)

    print(f"[Fetch] {len(unique)} unique articles")

    results = []
    batch_size = 10
    for i in range(0, min(len(unique), 30), batch_size):
        batch = unique[i:i + batch_size]
        try:
            results.extend(analyze_with_claude(batch))
        except Exception as ex:
            print(f"[Claude] Batch failed: {ex}")
            results.extend([{**item, "impactScore": 0, "impactLabel": "NEUTRAL",
                             "impactReasoning": str(ex), "keyRisks": [], "keyOpportunities": [],
                             "urgency": "MONITOR"} for item in batch])

    urgency_order = {"BREAKING": 0, "TODAY": 1, "THIS_WEEK": 2, "MONITOR": 3}
    results.sort(key=lambda x: (urgency_order.get(x["urgency"], 3), -abs(x.get("impactScore", 0))))

    _cache["data"] = results
    _cache["ts"] = now
    _cache["query"] = query
    return results


@app.route("/")
def index():
    return jsonify({"ok": True, "message": "API running. Use /api/news or /api/health."})



@app.route("/api/auth", methods=["POST"])
def auth():
    if not PASSPHRASE:
        return jsonify({"ok": False, "error": "PASSPHRASE env var not set on server"}), 500

    body = request.get_json(silent=True) or {}
    provided = body.get("password", "")

    if secrets.compare_digest(provided, PASSPHRASE):
        token = secrets.token_hex(32)
        _valid_tokens.add(token)
        return jsonify({"ok": True, "token": token})
    else:
        return jsonify({"ok": False, "error": "Invalid password"}), 401


@app.route("/api/news")
def news():
    query = request.args.get("q", "").strip()
    try:
        data = get_news(query)
        return jsonify({"ok": True, "count": len(data), "items": data})
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "api_key_set": bool(ANTHROPIC_API_KEY),
    })


if __name__ == "__main__":
    if not ANTHROPIC_API_KEY:
        print("\n⚠️  WARNING: ANTHROPIC_API_KEY is not set!")
        print("   Run: export ANTHROPIC_API_KEY=sk-ant-...\n")
    else:
        print(f"✓ API key loaded (...{ANTHROPIC_API_KEY[-4:]})")
    app.run(debug=True, port=5050)