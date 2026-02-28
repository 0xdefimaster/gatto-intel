import re
import requests
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime


# 1. HESAPLAR VE GÜNCEL INSTANCE'LAR
INTEL_ACCOUNTS = ["sentdefender", "war_monitor", "MonitorX99800", "visionergeo", "IranObserver0","DailyIranNews", "Conflict_Radar",]

NITTER_INSTANCES = [
    "https://nitter.catsarch.com",
    "https://nitter.privacyredirect.com",
    "https://nitter.tiekoetter.com",
    "https://xcancel.com",
    "https://lightbrd.com",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.kavin.rocks",
    "https://nitter.1d4.us",
    "https://nitter.unixfox.eu",
    "https://nitter.net",
    "https://nitter.perennialte.ch",
    "https://nitter.esmailelbob.xyz",
]

# ─────────────────────────────────────────────────────────────────────────────
# PLAN B — YEDEK RSS KAYNAKLARI (Nitter tamamen ölüyse devreye girer)
# Sadece çatışma/savunma/istihbarat odaklı — spor/magazin haberleri gelmez
# ─────────────────────────────────────────────────────────────────────────────

FALLBACK_RSS_SOURCES = [
    {"url": "https://www.iranintl.com/en/rss",      "account": "IranIntl"},
    {"url": "https://liveuamap.com/rss",             "account": "LiveUAMap"},
    {"url": "https://understandingwar.org/rss.xml",  "account": "ISW"},
    {"url": "https://www.middleeasteye.net/rss",     "account": "MiddleEastEye"},
    {"url": "https://www.defensenews.com/rss/",      "account": "DefenseNews"},
    {"url": "https://thedefensepost.com/feed/",      "account": "DefensePost"},
]

IMPACT_KEYWORDS = {
    # --- Üst Düzey Savaş Sinyalleri (Kritik) ---
    "notam":        12,  # Hava sahası kapatma (Saldırı öncesi en net işaret)
    "nuclear":      10,  # Nükleer tesis veya tehdit söylemi
    "ballistic":    10,  # Balistik füze fırlatışı
    "spoofing":     9,   # GPS karartma (Savunma sistemleri devrede demek)
    "centcom":      8,   # ABD Merkez Komutanlığı resmi hareketliliği
    "preemptive":   8,   # Önleyici vuruş terimi
    
    # --- Askeri Varlıklar ve Birimler ---
    "f-35":          6,  # F-35 ABD savaş uçağı
    "carrier group": 7,  # Uçak gemisi görev grubu sevkiyatı
    "b-52":         7,   # Stratejik bombardıman uçakları
    "kc-135":       6,   # Yakıt ikmali (Hava operasyonu hazırlığı)
    "iron dome":    7,   # Demir Kubbe aktivasyonu
    "irgc":         8,   # Devrim Muhafızları (İran tarafı hareketliliği)
    "idf":          6,   # İsrail Savunma Kuvvetleri açıklamaları
    
    # --- Coğrafi Sinyaller ---
    "hormuz":       8,   # Hürmüz Boğazı (Küresel kriz tetikleyici)
    "red sea":      6,   # Kızıldeniz/Husi hareketliliği
    "natanz":       8,   # İran nükleer tesisi
    "tel aviv":     5,   # Doğrudan hedef şehir
    "tehran":       5,   # Doğrudan hedef şehir
    "uss gerald":   7,   # Abd savaş uçak gemisi
    "uss abraham":  7,   # Abd savaş uçağı gemisi
    "afghanistan":  3,  
    
    # --- Genel Çatışma Terimleri (Puanlar Optimize Edildi) ---
    "missile":      8, 
    "airstrike":    8, 
    "explosion":    7,
    "intercepted":  6,   # Füzenin havada vurulması
    "retaliation":  7,   # Misilleme yemini
    "mobilization": 6,   # Seferberlik ilanı
    "war":          5,
    "hezbollah":    7,
    "houthi":       7,
    "syria":        4,
    "lebanon":      4,
    "iran":         3,
    "abd":          3,
    "israeli":      3, 
    "conflict ":    9,
}

# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────────────────────────────────────

def normalize(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', '', t)
    return re.sub(r'\s+', ' ', t).strip()

def is_duplicate(new_title: str, seen: list, threshold: float = 0.82) -> tuple:
    norm_new = normalize(new_title)
    words_new = set(norm_new.split())
    for i, old in enumerate(seen):
        norm_old = normalize(old)
        words_old = set(norm_old.split())
        if norm_new == norm_old or (words_new & words_old and len(words_new & words_old) / len(words_new | words_old) >= 0.65):
            return True, i
    return False, -1

def score_title(text: str) -> tuple:
    # Metni tamamen küçük harfe çekiyoruz (Case-sensitivity sorunu bitti)
    tl = text.lower()
    total, hits = 0, []
    
    # 1. Standart Anahtar Kelime Puanlaması
    for kw, pts in IMPACT_KEYWORDS.items():
        if kw in tl:
            total += pts
            hits.append(kw)
            
    # 2. YENİ: ÖZEL KOMBİNASYON BONUSU (Iran + Strike)
    # Eğer metinde hem 'iran' hem 'strike' geçiyorsa +5 puan ekle
    if "iran" in tl and "strike" in tl:
        total += 5
        hits.append("iran+strike_bonus") # Takip edebilmek için hit listesine ekledik
        
    return total, hits

# ─────────────────────────────────────────────────────────────────────────────
# RSS ÇEKİCİ (GÜÇLENDİRİLMİŞ)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rss(url: str, label: str, limit: int = 10) -> list:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    }
    try:
        # SSL hatalarını ve timeout'ları yönetmek için verify=False ve timeout=12
        resp = requests.get(url, headers=headers, timeout=12, verify=False)
        resp.raise_for_status()
        
        # XML Ayrıştırma
        root = ET.fromstring(resp.content)
        items = []
        
        # Nitter'ın gönderdiği XML'de itemlar genellikle .//item altında bulunur
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            desc = item.findtext("description", "").strip()
            link = item.findtext("link", "").strip()
            pubdate = item.findtext("pubDate", "").strip()

            if title or desc:
                # HTML temizliği
                clean_desc = re.sub(r'<[^>]+>', '', desc).strip()
                items.append({
                    "title": title,
                    "description": clean_desc,
                    "link": link,
                    "pubDate": pubdate
                })
            if len(items) >= limit: break
            
        print(f"[RSS ✓] {label} → {len(items)} öğe alındı.")
        return items
    except Exception as e:
        print(f"[RSS ✗] {label}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# NITTER CANLILIK KONTROLÜ (program başında bir kez yapılır)
# ─────────────────────────────────────────────────────────────────────────────

_NITTER_ALIVE     = None     # None=test edilmedi, True=çalışıyor, False=ölü
_NITTER_ALIVE_TS  = 0        # Son kontrol zamanı (unix timestamp)
_NITTER_ALIVE_TTL = 15 * 60  # 15 dakikada bir yeniden kontrol et
_NITTER_WORKING   = None     # Çalışan instance — her hesap için tekrar aramayı önler

def _check_nitter_alive() -> bool:
    global _NITTER_ALIVE, _NITTER_ALIVE_TS, _NITTER_WORKING
    import time as _time
    now = _time.time()

    # TTL dolmamışsa önbelleği kullan
    if _NITTER_ALIVE is not None and (now - _NITTER_ALIVE_TS) < _NITTER_ALIVE_TTL:
        return _NITTER_ALIVE

    # Yeniden kontrol (ilk çalışma veya 15dk sonra)
    print("[~] Nitter canlılık kontrolü yapılıyor...")
    for instance in NITTER_INSTANCES:
        try:
            url = f"{instance.rstrip('/')}/sentdefender/rss"
            r = requests.get(url, timeout=3, verify=False,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and b"<rss" in r.content[:300]:
                print(f"[✓] Nitter aktif: {instance}")
                _NITTER_ALIVE    = True
                _NITTER_ALIVE_TS = now
                _NITTER_WORKING  = instance
                return True
        except:
            pass

    print("[!] Nitter ölü → Plan B: Fallback RSS devreye giriyor.")
    _NITTER_ALIVE    = False
    _NITTER_ALIVE_TS = now
    _NITTER_WORKING  = None
    return False

def fetch_fallback_sources(limit: int = 15) -> list:
    """Plan B: Fallback RSS kaynaklarından veri çeker."""
    from email.utils import parsedate_to_datetime
    all_items = []
    for source in FALLBACK_RSS_SOURCES:
        items = fetch_rss(source["url"], source["account"], limit=limit)
        for item in items:
            item["account"] = source["account"]
            # Timestamp eksikse pubDate'den üret
            if "timestamp" not in item or item["timestamp"] == 0:
                try:
                    dt = parsedate_to_datetime(item.get("pubDate", ""))
                    item["timestamp"] = dt.timestamp()
                except:
                    item["timestamp"] = 0
        all_items.extend(items)
    return all_items

def fetch_account(account: str, limit: int = 10) -> list:
    # Çalışan instance varsa önce onu dene
    instances = NITTER_INSTANCES[:]
    if _NITTER_WORKING and _NITTER_WORKING in instances:
        instances.remove(_NITTER_WORKING)
        instances.insert(0, _NITTER_WORKING)

    for instance in instances:
        url = f"{instance.rstrip('/')}/{account}/rss"
        results = fetch_rss(url, f"nitter/@{account}", limit=limit)
        if results:
            for r in results:
                r["account"] = account
            return results
    return []

# ─────────────────────────────────────────────────────────────────────────────
# SAVAŞ ENDEKSİ HESAPLAMA (TEK VE TEMİZ VERSİYON)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_war_index(per_account_limit: int = 15) -> tuple:
    total_score = 0
    signals = []
    seen_titles = []

    # Plan A: Nitter çalışıyor mu? Değilse Plan B: Fallback RSS
    if _check_nitter_alive():
        source_items = []
        for account in INTEL_ACCOUNTS:
            source_items.extend(fetch_account(account, limit=per_account_limit))
        mode = "Nitter"
    else:
        source_items = fetch_fallback_sources(limit=per_account_limit)
        mode = "Fallback RSS"

    for item in source_items:
            full_text = f"{item.get('title', '')} {item.get('description', '')}"

            pts, hits = score_title(full_text)
            if pts == 0: continue

            # Kopya Kontrolü
            dup, dup_idx = is_duplicate(full_text, seen_titles)
            if dup:
                if 0 <= dup_idx < len(signals):
                    asb = signals[dup_idx].setdefault("also_shared_by", [])
                    if item.get("account") not in asb: asb.append(item.get("account"))
                continue

            # Zaman damgasını işle
            try:
                dt = parsedate_to_datetime(item.get("pubDate", ""))
                ts = dt.timestamp()
            except: ts = 0

            seen_titles.append(full_text)
            total_score += pts
            
            signals.append({
                "account": item.get("account", "unknown"),
                "text": full_text[:160].replace('\n', ' ') + "...",
                "title": item.get('title', '')[:120],
                "link": item.get("link", ""),
                "pubDate": item.get("pubDate", ""),
                "timestamp": ts,
                "score": pts,
                "keywords": hits,
                "also_shared_by": [],
            })

    # En yeni sinyalleri üstte göster
    signals.sort(key=lambda x: x["timestamp"], reverse=True)

    SCORE_CEILING = 560
    bar_pct = min(int(total_score / SCORE_CEILING * 100), 100)
    
    for s in signals:
        s["raw_total"] = total_score

    print(f"\n[GATTO] Tarama Bitti [{mode}] | Skor: {total_score} | Bar: %{bar_pct} | Sinyal: {len(signals)}\n")
    return bar_pct, signals


# ─────────────────────────────────────────────────────────────────────────────
# BOT İÇİN RAW VERİ ÇEKİCİ (POSTER_BOT İÇİN GEREKLİ)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_raw_posts(limit_per_account: int = 10) -> list:
    """Tüm hesaplardan ham gönderileri toplar ve bir liste olarak döner."""
    all_raw_posts = []
    if _check_nitter_alive():
        print(f"[SCRAPER] Plan A: Nitter — {len(INTEL_ACCOUNTS)} hesap taranıyor...")
        for account in INTEL_ACCOUNTS:
            items = fetch_account(account, limit=limit_per_account)
            for item in items:
                item["account"] = account
            all_raw_posts.extend(items)
    else:
        print(f"[SCRAPER] Plan B: Fallback RSS — {len(FALLBACK_RSS_SOURCES)} kaynak taranıyor...")
        all_raw_posts = fetch_fallback_sources(limit=limit_per_account)

    return all_raw_posts
