"""
app.py — Gatto Intelligence v4.2
- Sinyal ve Haberler gerçek zaman damgasına (timestamp) göre sıralanır.
- Haber cache: 30 dakika / Sinyal cache: 15 dakika.
- En yeni içerik her zaman en üstte görünür.
"""

import os
import json
import re
import time
import requests
import xml.etree.ElementTree as ET
from flask import Flask, render_template, jsonify
from datetime import datetime
from scraper import calculate_war_index
from email.utils import parsedate_to_datetime

app = Flask(__name__)

# Önbellek Ayarları
CACHE_FILE          = "gatto_cache.json"
SIGNAL_CACHE_FILE   = "signal_cache.json"
CACHE_DURATION      = 30 * 60   # 30 dakika (haberler)
SIGNAL_CACHE_DURATION = 15 * 60 # 15 dakika (sinyaller)

# Haber Kaynakları
NEWS_SOURCES = [
    {"url": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best", "tag": "REUTERS", "filter": True},
    {"url": "https://feeds.reuters.com/reuters/worldNews",         "tag": "REUTERS",     "filter": True},
    {"url": "http://feeds.bbci.co.uk/news/world/rss.xml", "tag": "BBC", "filter": True},
    {"url": "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "tag": "BBC·ME", "filter": False},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",           "tag": "ALJAZEERA",   "filter": True},
    {"url": "https://rsshub.app/apnews/topics/world-news",         "tag": "AP",          "filter": True},
    {"url": "https://www.theguardian.com/world/middleeast/rss",    "tag": "GUARDIAN·ME", "filter": False},
    {"url": "https://www.defensenews.com/arc/outboundfeeds/rss/",  "tag": "DEFNEWS",     "filter": False},
    {"url": "https://www.middleeasteye.net/rss",                   "tag": "MEE",         "filter": False},
    {"url": "https://www.jpost.com/rss/rssfeedsFrontPage.aspx",    "tag": "JPOST",       "filter": True},
    {"url": "https://www.timesofisrael.com/feed/",                 "tag": "TOI",         "filter": True},
]

REQUIRED_KEYWORDS = {
    "iran", "hormuz", "strait", "tehran", "irgc", "persian gulf", "conflict ",
    "israel", "idf", "netanyahu", "gaza", "f-35", "hamas",
    "hezbollah", "houthi", "yemen", "syria", "lebanon",
    "missile", "rocket", "airstrike", "drone", "warship", "tanker",
    "nuclear", "military", "attack", "explosion", "offensive", "invasion",
    "oil price", "crude oil", "brent", "c-5m"
}

BLACKLIST_TITLE = {
    "nfl", "nba", "soccer", "football", "basketball", "baseball", "tennis",
    "oscar", "grammy", "celebrity", "kardashian", "taylor swift",
    "recipe", "cooking", "fashion", "netflix", "disney",
    "stock market", "dow jones", "nasdaq", "hurricane", "covid",
}

def passes_filter(title: str, description: str, use_filter: bool) -> bool:
    text_lower  = (title + " " + (description or "")).lower()
    title_lower = title.lower()
    for bad in BLACKLIST_TITLE:
        if bad in title_lower:
            return False
    if not use_filter:
        return True
    return any(kw in text_lower for kw in REQUIRED_KEYWORDS)

# ─────────────────────────────────────────────────────────────────────────────
# RSS PARSER
# ─────────────────────────────────────────────────────────────────────────────
def fetch_rss_news(source: dict) -> list:
    url        = source["url"]
    tag        = source["tag"]
    use_filter = source.get("filter", True)

    try:
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (GattoIntel/4.2; +https://thegatto.xyz)"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[RSS ✗] {tag}: {e}")
        return []

    articles = []
    for item in root.findall(".//item"):
        title   = item.findtext("title",       "").strip()
        link    = item.findtext("link",        "").strip()
        desc    = item.findtext("description", "").strip()
        pubdate = item.findtext("pubDate",     "").strip()
        desc    = re.sub(r'<[^>]+>', '', desc).strip()

        if not title or len(title) < 10:
            continue

        if passes_filter(title, desc, use_filter):
            articles.append({
                "title":       title,
                "description": desc[:200] if desc else "",
                "url":         link,
                "urlToImage":  None,
                "publishedAt": pubdate,
                "source":      {"name": tag},
                "_tag":        tag,
            })
    return articles

# ─────────────────────────────────────────────────────────────────────────────
# HABER CACHE & FETCH
# ─────────────────────────────────────────────────────────────────────────────
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) < CACHE_DURATION:
            return data
    except Exception:
        pass
    return None

def save_cache(articles):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(), "articles": articles},
                  f, ensure_ascii=False, indent=2)

def fetch_news():
    cached = load_cache()
    if cached:
        age = int(time.time() - cached["timestamp"])
        return cached["articles"], True, age

    all_articles = []
    for source in NEWS_SOURCES:
        all_articles.extend(fetch_rss_news(source))

    seen, unique = set(), []
    for a in all_articles:
        u = a.get("url", "")
        if u and u not in seen:
            seen.add(u)
            unique.append(a)

    # TARİH SIRALAMASI: En yeni haber en üstte
    def get_news_ts(x):
        try:
            return parsedate_to_datetime(x.get("publishedAt", "")).timestamp()
        except:
            return 0

    unique.sort(key=get_news_ts, reverse=True)
    top = unique[:15]

    print(f"[FEED] {len(top)} güncel haber dizildi.")
    save_cache(top)
    return top, False, 0

# ─────────────────────────────────────────────────────────────────────────────
# SİNYAL CACHE & FETCH
# ─────────────────────────────────────────────────────────────────────────────
def load_signal_cache():
    if not os.path.exists(SIGNAL_CACHE_FILE):
        return None
    try:
        with open(SIGNAL_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) < SIGNAL_CACHE_DURATION:
            return data
    except Exception:
        pass
    return None

def save_signal_cache(war_score, signals):
    with open(SIGNAL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.time(),
            "war_score": war_score,
            "signals":   signals,
        }, f, ensure_ascii=False, indent=2)

def fetch_signals():
    cached = load_signal_cache()
    if cached:
        age = int(time.time() - cached["timestamp"])
        if age < SIGNAL_CACHE_DURATION:
            return cached["war_score"], cached["signals"], True, age

    print("[SIGNAL] Nitter taranıyor...")
    war_score, signals = calculate_war_index()

    if signals:
        save_signal_cache(war_score, signals)
        return war_score, signals, False, 0
    elif cached:
        return cached["war_score"], cached["signals"], True, int(time.time() - cached["timestamp"])
    
    return 0, [], False, 0

# ─────────────────────────────────────────────────────────────────────────────
# TOKEN DATA
# ─────────────────────────────────────────────────────────────────────────────
# BURN_DATA ismini TOKEN_DATA olarak düşünebilirsin
BURN_DATA = {
    "token":      "$Gatto Intel",
    "ticker":     "Windex",
    "description": "Global instability meets on-chain intelligence. $WNDX is the native utility token of the War Index (Windex) ecosystem...",
    "ca":         "9qLhkx5dpCoPRqg89JASKpkFoVLqTywC2Gsu2XTMpump",
    "buy_url":    "https://pump.fun/coin/9qLhkx5dpCoPRqg89JASKpkFoVLqTywC2Gsu2XTMpump"
}

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    articles, from_cache, cache_age = fetch_news()
    war_score, intelligence_signals, sig_cached, sig_age = fetch_signals()

    # Ek bir güvenlik olarak sinyalleri burada da tarihe göre dizelim
    intelligence_signals.sort(key=lambda x: x.get('timestamp', 0), reverse=True)

    return render_template(
        "index.html",
        articles         = articles,
        burn             = BURN_DATA,
        war_score        = war_score,
        intel_sources    = intelligence_signals,
        from_cache       = from_cache,
        cache_age_min    = cache_age // 60,
        sig_cached       = sig_cached,
        sig_age_min      = sig_age // 60,
        now              = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        api_ok           = True,
    )

@app.route("/api/war-index")
def api_war_index():
    score, signals, _, _ = fetch_signals()
    return jsonify({"war_index": score, "signals": signals})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
