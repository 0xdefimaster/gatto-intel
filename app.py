"""
app.py — Gatto Intelligence v5.2
Routes: / (home), /intel, /map, /news, /about
API:    /api/war-index, /api/news, /api/map-events, /api/mil-assets

Legal compliance notes:
- News: public RSS feeds, headline + 200-char summary only (fair use / news aggregation)
- Signal data: publicly available Twitter/X content via Nitter RSS
- Map tiles: OpenFreeMap / MapLibre GL JS (BSD-2)
- Fonts: Google Fonts (OFL)
- Military asset data: OSINT / publicly reported positions (@EGYOSINT, USNI News, NavyTimes)
- No third-party logos, images, or full article text reproduced
"""

import os, json, re, time, requests, xml.etree.ElementTree as ET
from flask import Flask, render_template, jsonify
from datetime import datetime
from scraper import calculate_war_index
from email.utils import parsedate_to_datetime

app = Flask(__name__)

CACHE_FILE            = "gatto_cache.json"
SIGNAL_CACHE_FILE     = "signal_cache.json"
CACHE_DURATION        = 30 * 60
SIGNAL_CACHE_DURATION = 15 * 60

NEWS_SOURCES = [
    {"url": "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best", "tag": "REUTERS",     "filter": True},
    {"url": "https://feeds.reuters.com/reuters/worldNews",                                      "tag": "REUTERS",     "filter": True},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",                                      "tag": "BBC",         "filter": True},
    {"url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",                          "tag": "BBC·ME",      "filter": False},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",                                        "tag": "ALJAZEERA",   "filter": True},
    {"url": "https://rsshub.app/apnews/topics/world-news",                                      "tag": "AP",          "filter": True},
    {"url": "https://www.theguardian.com/world/middleeast/rss",                                 "tag": "GUARDIAN·ME", "filter": False},
    {"url": "https://www.defensenews.com/arc/outboundfeeds/rss/",                               "tag": "DEFNEWS",     "filter": False},
    {"url": "https://www.middleeasteye.net/rss",                                                "tag": "MEE",         "filter": False},
    {"url": "https://www.jpost.com/rss/rssfeedsFrontPage.aspx",                                 "tag": "JPOST",       "filter": True},
    {"url": "https://www.timesofisrael.com/feed/",                                              "tag": "TOI",         "filter": True},
]

REQUIRED_KEYWORDS = {
    "iran","hormuz","strait","tehran","irgc","persian gulf","conflict",
    "israel","idf","netanyahu","gaza","f-35","hamas","hezbollah","houthi",
    "yemen","syria","lebanon","missile","rocket","airstrike","drone","warship",
    "tanker","nuclear","military","attack","explosion","offensive","invasion",
    "oil price","crude oil","brent","c-5m",
}

BLACKLIST_TITLE = {
    "nfl","nba","soccer","football","basketball","baseball","tennis",
    "oscar","grammy","celebrity","kardashian","taylor swift",
    "recipe","cooking","fashion","netflix","disney",
    "stock market","dow jones","nasdaq","hurricane","covid",
}

def passes_filter(title, description, use_filter):
    tl = (title + " " + (description or "")).lower()
    for bad in BLACKLIST_TITLE:
        if bad in title.lower(): return False
    if not use_filter: return True
    return any(kw in tl for kw in REQUIRED_KEYWORDS)

# ── MAP DATA ──────────────────────────────────────────────────────────────────
GEO_KEYWORDS = {
    "iran":         [32.0,  53.0,  "Iran"],
    "tehran":       [35.7,  51.4,  "Tehran"],
    "natanz":       [33.5,  51.9,  "Natanz"],
    "hormuz":       [26.6,  56.3,  "Strait of Hormuz"],
    "persian gulf": [26.0,  54.0,  "Persian Gulf"],
    "israel":       [31.5,  34.8,  "Israel"],
    "tel aviv":     [32.1,  34.8,  "Tel Aviv"],
    "gaza":         [31.3,  34.3,  "Gaza"],
    "lebanon":      [33.9,  35.5,  "Lebanon"],
    "beirut":       [33.9,  35.5,  "Beirut"],
    "syria":        [34.8,  38.8,  "Syria"],
    "damascus":     [33.5,  36.3,  "Damascus"],
    "yemen":        [15.6,  48.5,  "Yemen"],
    "red sea":      [20.0,  38.0,  "Red Sea"],
    "ukraine":      [49.0,  32.0,  "Ukraine"],
    "kyiv":         [50.4,  30.5,  "Kyiv"],
    "russia":       [61.5, 105.0,  "Russia"],
    "moscow":       [55.8,  37.6,  "Moscow"],
    "taiwan":       [23.7, 121.0,  "Taiwan"],
    "china":        [35.0, 105.0,  "China"],
    "iraq":         [33.2,  43.7,  "Iraq"],
    "baghdad":      [33.3,  44.4,  "Baghdad"],
    "saudi":        [24.0,  45.0,  "Saudi Arabia"],
    "pakistan":     [30.4,  69.3,  "Pakistan"],
    "afghanistan":  [33.9,  67.7,  "Afghanistan"],
}

ICON_KEYWORDS = {
    "f-35":"fighter","f-16":"fighter","aircraft":"fighter",
    "airstrike":"airstrike","strike":"airstrike",
    "missile":"missile","ballistic":"missile","rocket":"missile",
    "explosion":"explosion","blast":"explosion",
    "warship":"warship","carrier":"warship","naval":"warship",
    "drone":"drone","uav":"drone",
    "nuclear":"nuclear","enrichment":"nuclear","natanz":"nuclear",
    "troops":"troops","mobilization":"troops","forces":"troops",
    "notam":"notam","airspace":"notam",
    "irgc":"irgc","hezbollah":"hezbollah","houthi":"houthi",
}

ICON_META = {
    "fighter":   {"symbol":"✈",  "color":"#00FF41","label":"Fighter Aircraft"},
    "airstrike": {"symbol":"💥", "color":"#ff4400","label":"Airstrike / Attack"},
    "missile":   {"symbol":"🚀", "color":"#ff2d2d","label":"Missile Launch"},
    "explosion": {"symbol":"💣", "color":"#ff6600","label":"Explosion Reported"},
    "warship":   {"symbol":"⚓", "color":"#00aaff","label":"Naval Movement"},
    "drone":     {"symbol":"🛸", "color":"#ffb300","label":"Drone Activity"},
    "nuclear":   {"symbol":"☢",  "color":"#ff00ff","label":"Nuclear Activity"},
    "troops":    {"symbol":"⚔",  "color":"#ffb300","label":"Troop Movement"},
    "notam":     {"symbol":"🚫", "color":"#ff2d2d","label":"NOTAM / Airspace Alert"},
    "irgc":      {"symbol":"🔴", "color":"#ff0000","label":"IRGC Activity"},
    "hezbollah": {"symbol":"🔶", "color":"#ff6600","label":"Hezbollah Activity"},
    "houthi":    {"symbol":"🟠", "color":"#ffaa00","label":"Houthi Activity"},
    "generic":   {"symbol":"⚠",  "color":"#ffb300","label":"Threat Signal"},
}

def extract_map_events(signals):
    events, seen = [], {}
    for sig in signals:
        text = (sig.get("title","") + " " + sig.get("text","")).lower()
        loc = next((geo for kw,geo in GEO_KEYWORDS.items() if kw in text), None)
        if not loc: continue
        itype = next((it for kw,it in ICON_KEYWORDS.items() if kw in text), "generic")
        lat, lng = loc[0], loc[1]
        key = f"{lat:.1f}_{lng:.1f}_{itype}"
        if key in seen:
            seen[key] += 1
            lat += seen[key]*0.4*(1 if seen[key]%2==0 else -1)
        else:
            seen[key] = 0
        m = ICON_META.get(itype, ICON_META["generic"])
        events.append({
            "lat":lat, "lng":lng, "region":loc[2], "icon_type":itype,
            "type":itype,                         # map.html JS bunu kullanıyor
            "symbol":m["symbol"], "color":m["color"], "label":m["label"],
            "title":sig.get("title","")[:100], "score":sig.get("score",0),
            "account":sig.get("account",""),
        })
    return events

# ── MILITARY ASSETS ───────────────────────────────────────────────────────────
# Kaynak: @EGYOSINT OSINT infographic (Jan 2025) + USNI News / NavyTimes
# /api/mil-assets endpoint'i üzerinden haritaya 5 dk'da bir beslenir.
# Gerçek zamanlı güncelleme için bu listeyi kendi intel_sources parse'ından
# dolduracak bir fonksiyon ekleyebilirsin (yorum satırlarına bak).

MIL_ASSETS = [
    # ── CARRIERS ──
    {"id":"cvn-72","name":"USS Abraham Lincoln (CVN-72)","cls":"carrier",
     "lat":22.0,"lng":62.0,"symbol":"⛵",
     "status":"Abraham Lincoln CSG — Arabian Sea. Counter-Iran posture.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"cvn-78","name":"USS Gerald R. Ford (CVN-78)","cls":"carrier",
     "lat":35.5,"lng":28.0,"symbol":"⛵",
     "status":"Gerald R. Ford CSG — Eastern Mediterranean. On Deployment.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"cvn-71","name":"USS Theodore Roosevelt (CVN-71)","cls":"carrier",
     "lat":33.5,"lng":26.0,"symbol":"⛵",
     "status":"Mediterranean Sea.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    # ── ESCORTS ──
    {"id":"ddg-119","name":"USS Frank E. Petersen Jr. (DDG-119)","cls":"escort",
     "lat":22.2,"lng":62.3,"symbol":"🚢",
     "status":"Escort — Abraham Lincoln CSG. Arabian Sea.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-101","name":"USS Gridley / Spruance","cls":"escort",
     "lat":21.8,"lng":61.8,"symbol":"🚢",
     "status":"Escort — Abraham Lincoln CSG. Arabian Sea.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-112","name":"USS Michael Murphy (DDG-112)","cls":"escort",
     "lat":22.4,"lng":62.5,"symbol":"🚢",
     "status":"Escort — Abraham Lincoln CSG. Arabian Sea.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-81","name":"USS Winston S. Churchill (DDG-81)","cls":"escort",
     "lat":35.7,"lng":28.2,"symbol":"🚢",
     "status":"Escort — Gerald R. Ford CSG.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-96","name":"USS Bainbridge (DDG-96)","cls":"escort",
     "lat":35.3,"lng":27.8,"symbol":"🚢",
     "status":"Escort — Gerald R. Ford CSG.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-72","name":"USS Mahan (DDG-72)","cls":"escort",
     "lat":35.6,"lng":28.4,"symbol":"🚢",
     "status":"Escort — Gerald R. Ford CSG.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-mcfaul","name":"USS McFaul (DDG-74)","cls":"escort",
     "lat":26.5,"lng":56.5,"symbol":"🚢",
     "status":"Persian Gulf — presence ops.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-mitscher","name":"USS Mitscher (DDG-57)","cls":"escort",
     "lat":27.0,"lng":57.0,"symbol":"🚢",
     "status":"Persian Gulf.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"ddg-delbert","name":"USS Delbert D. Black","cls":"escort",
     "lat":15.5,"lng":42.5,"symbol":"🚢",
     "status":"Red Sea.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    # ── SUBMARINE ──
    {"id":"ssbn-ohio","name":"Ohio-class SSBN (location unknown)","cls":"submarine",
     "lat":24.0,"lng":58.0,"symbol":"🔱",
     "status":"Ohio-class SSBN — unknown location. Estimated Arabian Sea / Persian Gulf per OSINT.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    # ── AIR BASES ──
    {"id":"base-muwaffaq","name":"Muwaffaq Salti AB (Jordan)","cls":"base",
     "lat":32.2,"lng":36.8,"symbol":"🔺",
     "status":"A-10 Thunderbolt + 24×F-15E + 30×F-35A + 20-22×KC-135/KC-46 tankers + 6×EA-18G Growler.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-psab","name":"Prince Sultan AB (Saudi Arabia)","cls":"base",
     "lat":24.1,"lng":47.6,"symbol":"🔺",
     "status":"3×E-11A BACN. F-16 deployed at UAE/KSA.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-aldhafra","name":"Al Dhafra AB (UAE)","cls":"base",
     "lat":24.2,"lng":54.5,"symbol":"🔺",
     "status":"F-16 deployment. Part of UAE/KSA air package.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-aludeid","name":"Al Udeid AB (Qatar)","cls":"base",
     "lat":25.1,"lng":51.3,"symbol":"🔺",
     "status":"USAF CENTCOM FWD HQ. THAAD battery + MIM-104 Patriot multiple batteries across region.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-chania","name":"Souda Bay / Chania (Greece)","cls":"base",
     "lat":35.5,"lng":24.1,"symbol":"🔺",
     "status":"1×RC-135 SIGINT operating.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-mildenhall","name":"RAF Mildenhall (UK)","cls":"base",
     "lat":52.4,"lng":0.5,"symbol":"🔺",
     "status":"WC-135 Nuke Sniffer + 2×E-3 Sentry AWACS. ~160 C-17 + 18×C-5M flights since Jan 16.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-dg","name":"Diego Garcia (BIOT)","cls":"base",
     "lat":-7.3,"lng":72.4,"symbol":"🔺",
     "status":"4×MC/HC-130J (2x here). B-2 Spirit forward ops hub. Critical Indian Ocean base.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-fairford","name":"RAF Fairford (UK)","cls":"base",
     "lat":51.7,"lng":-1.8,"symbol":"🔺",
     "status":"B-52H Stratofortress rotational deployment. NATO deterrence.",
     "source":"USAF/RAF","date":"2025-01-18"},

    {"id":"base-djibouti","name":"Camp Lemonnier (Djibouti)","cls":"base",
     "lat":11.5,"lng":43.1,"symbol":"🔺",
     "status":"CJTF-HOA / AFRICOM hub. Drone + ISR ops. Gulf of Aden access.",
     "source":"@EGYOSINT","date":"2025-01-18"},

    {"id":"base-incirlik","name":"İncirlik AB (Turkey)","cls":"base",
     "lat":37.0,"lng":35.4,"symbol":"🔺",
     "status":"USAF 39th ABW. B61 nuclear weapons (NATO dual-key).",
     "source":"NATO/USAF","date":"2025-01-18"},

    # ── AIR DEFENSE ──
    {"id":"thaad-ksa","name":"THAAD Battery (KSA/Qatar)","cls":"air-defense",
     "lat":25.3,"lng":49.8,"symbol":"🛡",
     "status":"At least 1 additional THAAD battery deployed KSA or Qatar. MIM-104 Patriot multiple batteries across region.",
     "source":"@EGYOSINT","date":"2025-01-18"},
]

def get_mil_assets():
    """
    Askeri varlık listesini döndür.
    İleride intel_sources'tan otomatik güncelleme eklemek için:
        live = parse_mil_positions_from_signals(signals)
        for item in live:
            existing = next((a for a in MIL_ASSETS if a["id"]==item["id"]), None)
            if existing: existing.update(item)
            else: MIL_ASSETS.append(item)
    """
    return MIL_ASSETS

# ── RSS ───────────────────────────────────────────────────────────────────────
def fetch_rss_news(source):
    try:
        resp = requests.get(source["url"], timeout=8,
            headers={"User-Agent":"Mozilla/5.0 (GattoIntel/5.2; +https://thegatto.xyz)"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[RSS ✗] {source['tag']}: {e}"); return []
    arts = []
    for item in root.findall(".//item"):
        title   = item.findtext("title","").strip()
        link    = item.findtext("link","").strip()
        desc    = re.sub(r'<[^>]+>','',item.findtext("description","")).strip()
        pubdate = item.findtext("pubDate","").strip()
        if not title or len(title)<10: continue
        if passes_filter(title, desc, source.get("filter",True)):
            arts.append({"title":title,"description":desc[:200],"url":link,
                "urlToImage":None,"publishedAt":pubdate,
                "source":{"name":source["tag"]},"_tag":source["tag"]})
    return arts

# ── CACHES ────────────────────────────────────────────────────────────────────
def load_cache():
    if not os.path.exists(CACHE_FILE): return None
    try:
        with open(CACHE_FILE,"r",encoding="utf-8") as f: data=json.load(f)
        if time.time()-data.get("timestamp",0)<CACHE_DURATION: return data
    except: pass
    return None

def save_cache(articles):
    with open(CACHE_FILE,"w",encoding="utf-8") as f:
        json.dump({"timestamp":time.time(),"articles":articles},f,ensure_ascii=False,indent=2)

def fetch_news():
    cached = load_cache()
    if cached:
        return cached["articles"], True, int(time.time()-cached["timestamp"])
    all_arts = []
    for s in NEWS_SOURCES: all_arts.extend(fetch_rss_news(s))
    seen,unique = set(),[]
    for a in all_arts:
        u = a.get("url","")
        if u and u not in seen: seen.add(u); unique.append(a)
    def ts(x):
        try: return parsedate_to_datetime(x.get("publishedAt","")).timestamp()
        except: return 0
    unique.sort(key=ts, reverse=True)
    top = unique[:30]
    save_cache(top)
    return top, False, 0

def load_signal_cache():
    if not os.path.exists(SIGNAL_CACHE_FILE): return None
    try:
        with open(SIGNAL_CACHE_FILE,"r",encoding="utf-8") as f: data=json.load(f)
        if time.time()-data.get("timestamp",0)<SIGNAL_CACHE_DURATION: return data
    except: pass
    return None

def save_signal_cache(score, signals):
    with open(SIGNAL_CACHE_FILE,"w",encoding="utf-8") as f:
        json.dump({"timestamp":time.time(),"war_score":score,"signals":signals},
                  f,ensure_ascii=False,indent=2)

def fetch_signals():
    cached = load_signal_cache()
    if cached:
        age = int(time.time()-cached["timestamp"])
        if age < SIGNAL_CACHE_DURATION:
            return cached["war_score"],cached["signals"],True,age
    war_score, signals = calculate_war_index()
    if signals: save_signal_cache(war_score, signals); return war_score,signals,False,0
    if cached: return cached["war_score"],cached["signals"],True,int(time.time()-cached["timestamp"])
    return 0,[],False,0

# ── TOKEN ─────────────────────────────────────────────────────────────────────
BURN_DATA = {
    "token":"Gatto Intel","ticker":"$WNDX",
    "description":"Global instability meets on-chain intelligence. $WNDX is the native utility token of the War Index ecosystem.",
    "ca":"9qLhkx5dpCoPRqg89JASKpkFoVLqTywC2Gsu2XTMpump",
    "buy_url":"https://pump.fun/coin/9qLhkx5dpCoPRqg89JASKpkFoVLqTywC2Gsu2XTMpump",
}

# ── ROUTES ────────────────────────────────────────────────────────────────────
def _now(): return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

@app.route("/")
def home():
    arts,fc,ca = fetch_news()
    ws,sigs,sc,sa = fetch_signals()
    sigs.sort(key=lambda x:x.get("timestamp",0),reverse=True)
    return render_template("home.html", active_page="home", articles=arts, burn=BURN_DATA,
        war_score=ws, intel_sources=sigs, from_cache=fc, cache_age_min=ca//60,
        sig_cached=sc, sig_age_min=sa//60, now=_now())

@app.route("/intel")
def intel():
    arts,fc,ca = fetch_news()
    ws,sigs,sc,sa = fetch_signals()
    sigs.sort(key=lambda x:x.get("timestamp",0),reverse=True)
    return render_template("intel.html", active_page="intel", articles=arts, burn=BURN_DATA,
        war_score=ws, intel_sources=sigs, now=_now())

@app.route("/map")
def map_view():
    ws,sigs,sc,sa = fetch_signals()
    sigs.sort(key=lambda x:x.get("timestamp",0),reverse=True)
    return render_template("map.html", active_page="map", burn=BURN_DATA,
        war_score=ws, intel_sources=sigs,
        map_events=extract_map_events(sigs), icon_meta=ICON_META, now=_now())

@app.route("/news")
def news():
    arts,fc,ca = fetch_news()
    ws,sigs,sc,sa = fetch_signals()
    return render_template("news.html", active_page="news", articles=arts, burn=BURN_DATA,
        war_score=ws, intel_sources=sigs)

@app.route("/about")
def about():
    ws,sigs,sc,sa = fetch_signals()
    return render_template("about.html", active_page="about", burn=BURN_DATA,
        war_score=ws, now=_now())

# ── API ROUTES ────────────────────────────────────────────────────────────────

@app.route("/api/war-index")
def api_war_index():
    s,sigs,_,_ = fetch_signals()
    return jsonify({"war_index":s,"signals":sigs})

@app.route("/api/news")
def api_news():
    a,_,_ = fetch_news()
    return jsonify({"articles":a,"count":len(a)})

@app.route("/api/map-events")
def api_map_events():
    """Harita JS'i 3 dkda bir bu endpoint'i çeker ve event marker'larını günceller."""
    _,sigs,__,___ = fetch_signals()
    events = extract_map_events(sigs)
    resp = jsonify(events)
    resp.headers["Cache-Control"] = "no-cache, max-age=180"
    return resp

@app.route("/api/mil-assets")
def api_mil_assets():
    """Harita JS'i 5 dkda bir bu endpoint'i çeker ve askeri varlıkları günceller."""
    resp = jsonify(get_mil_assets())
    resp.headers["Cache-Control"] = "no-cache, max-age=300"
    return resp

# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=True)
