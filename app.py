"""
app.py — Gatto Intelligence v4.0
NewsAPI tamamen kaldırıldı.
Reuters, BBC, Al Jazeera, AP gibi güvenilir kaynaların
RSS feed'lerinden doğrudan haber çekilir.
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

app = Flask(__name__)

CACHE_FILE     = "gatto_cache.json"
CACHE_DURATION = 30 * 60   # 30 dakika

# ─────────────────────────────────────────────────────────────────────────────
# HABER RSS KAYNAKLARI
# Her kaynak için: url, etiket, opsiyonel keyword filtresi
# ─────────────────────────────────────────────────────────────────────────────
NEWS_SOURCES = [
    # Reuters
    {"url": "https://feeds.reuters.com/reuters/worldNews",         "tag": "REUTERS",     "filter": True},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",         "tag": "REUTERS",     "filter": True},
    # BBC World
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",         "tag": "BBC",         "filter": True},
    {"url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "tag": "BBC·ME",  "filter": False},  # Zaten bölgesel
    # Al Jazeera
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",           "tag": "ALJAZEERA",   "filter": True},
    # AP News
    {"url": "https://rsshub.app/apnews/topics/world-news",         "tag": "AP",          "filter": True},
    # The Guardian – Middle East
    {"url": "https://www.theguardian.com/world/middleeast/rss",    "tag": "GUARDIAN·ME", "filter": False},
    # Defense News
    {"url": "https://www.defensenews.com/arc/outboundfeeds/rss/",  "tag": "DEFNEWS",     "filter": False},
    # Middle East Eye
    {"url": "https://www.middleeasteye.net/rss",                   "tag": "MEE",         "filter": False},
    # Jerusalem Post
    {"url": "https://www.jpost.com/rss/rssfeedsFrontPage.aspx",    "tag": "JPOST",       "filter": True},
    # Times of Israel
    {"url": "https://www.timesofisrael.com/feed/",                 "tag": "TOI",         "filter": True},
]

# ─────────────────────────────────────────────────────────────────────────────
# ZORUNLU KELIMELER — bunlardan en az 1'i başlık+desc'te olmalı
# filter=True olan kaynaklar için uygulanır
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED_KEYWORDS = {
    # Coğrafya / aktörler
    "iran", "hormuz", "Hormuz " , "strait", "tehran", "irgc", "persian gulf",
    "israel", "idf", "netanyahu", "gaza", "west bank", "hamas",
    "hezbollah", "houthi", "yemen", "iraq", "syria", "lebanon",
    "saudi", "riyadh", "gulf",
    # Askeri / çatışma
    "missile", "rocket", "airstrike", "air strike", "strike",
    "drone", "warship", "tanker", "blockade", "naval",
    "nuclear", "enrichment", "uranium", "sanction",
    "military", "troops", "attack", "explosion", "bomb",
    "ceasefire", "offensive", "invasion",
    # Enerji / ekonomi (bölgesel)
    "oil price", "crude oil", "brent", "opec",
}

# Bu kelimeler başlıkta varsa → kesin at
BLACKLIST_TITLE = {
    "nfl", "nba", "soccer", "football", "basketball", "baseball", "tennis",
    "oscar", "grammy", "celebrity", "kardashian", "taylor swift",
    "recipe", "cooking", "fashion", "netflix", "disney",
    "jesse jackson", "vinfas", "base oil industry",
    "india france", "pakistan crisis", "divine animal",
    "resale guarantee", "buyback guarantee",
    "stock market", "dow jones", "nasdaq",    # genel finans değil bölgesel
    "hurricane", "earthquake", "flood",       # doğal afet
    "covid", "vaccine", "cancer", "diabetes", # sağlık
}

def passes_filter(title: str, description: str, use_filter: bool) -> bool:
    text_lower = (title + " " + (description or "")).lower()
    title_lower = title.lower()

    # Kara liste kelime kontrolü (her kaynak için)
    for bad in BLACKLIST_TITLE:
        if bad in title_lower:
            return False

    # Filtre kapalıysa (zaten bölgesel kaynak) buraya kadar geçti
    if not use_filter:
        return True

    # Zorunlu keyword kontrolü
    return any(kw in text_lower for kw in REQUIRED_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# RSS PARSER
# ─────────────────────────────────────────────────────────────────────────────
def fetch_rss_news(source: dict) -> list:
    url      = source["url"]
    tag      = source["tag"]
    use_filter = source.get("filter", True)

    try:
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (GattoIntel/4.0; +https://thegatto.xyz)"}
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

        # HTML tag temizliği
        desc = re.sub(r'<[^>]+>', '', desc).strip()

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

    print(f"[RSS ✓] {tag}: {len(articles)} alakalı haber")
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# HABER ÇEKME (cache'li)
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

    # URL dedup
    seen, unique = set(), []
    for a in all_articles:
        u = a.get("url", "")
        if u and u not in seen:
            seen.add(u)
            unique.append(a)

    # Tarihe göre sırala (RSS pubDate string, basit sort yeterli)
    unique.sort(key=lambda x: x.get("publishedAt", ""), reverse=True)
    top = unique[:15]

    print(f"[FEED] {len(top)} temiz haber cache'lendi.")
    save_cache(top)
    return top, False, 0


# ─────────────────────────────────────────────────────────────────────────────
BURN_DATA = {
    "token":        "$GATTO",
    "total_burned": "1,420,690,000",
    "usd_burned":   "$42,069",
    "last_tx":      "5vA2...XzY9",
    "last_tx_url":  "https://solscan.io/tx/5vA2...",
    "burn_events":  42,
    "next_burn":    "TARGET: 2,000,000,000",
    "buy_url":      "https://raydium.io/swap/?inputMint=sol&outputMint=BURAYA_CA_GELECEK",
    "recent_txs": [
        {"hash": "4kL2...8mN1", "amount": "10M", "url": "https://solscan.io/tx/1"},
        {"hash": "2pQ5...9rS3", "amount": "5M",  "url": "https://solscan.io/tx/2"},
        {"hash": "7tV8...1wX4", "amount": "25M", "url": "https://solscan.io/tx/3"},
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    articles, from_cache, cache_age = fetch_news()
    war_score, intelligence_signals  = calculate_war_index()
    next_min = max(0, (CACHE_DURATION - cache_age) // 60)
    return render_template(
        "index.html",
        articles         = articles,
        burn             = BURN_DATA,
        war_score        = war_score,
        intel_sources    = intelligence_signals,
        from_cache       = from_cache,
        cache_age_min    = cache_age // 60,
        next_refresh_min = next_min,
        now              = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        api_ok           = True,
    )

@app.route("/api/war-index")
def api_war_index():
    score, signals = calculate_war_index()
    return jsonify({"war_index": score, "signals": signals})

@app.route("/api/burn")
def api_burn():
    return jsonify(BURN_DATA)

@app.route("/api/news/debug")
def api_news_debug():
    """Cache'i atlar, tüm kaynakları canlı test eder."""
    result = {}
    for source in NEWS_SOURCES:
        arts = fetch_rss_news(source)
        result[source["tag"] + "_" + source["url"][-30:]] = {
            "count":  len(arts),
            "titles": [a["title"] for a in arts[:5]],
        }
    return jsonify(result)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
