import re
import requests
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime


# 1. HESAPLAR VE GÜNCEL INSTANCE'LAR
INTEL_ACCOUNTS = ["sentdefender", "war_monitor", "MonitorX99800", "visionergeo", "IranObserver0","DailyIranNews", "Conflict_Radar",]

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.kavin.rocks",      # YENİ
    "https://nitter.1d4.us",            # YENİ
    "https://nitter.unixfox.eu",        # YENİ
    "https://nitter.net",       
    "https://nitter.perennialte.ch", # Şu an en stabillerden biri
    "https://nitter.esmailelbob.xyz",    
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

def fetch_account(account: str, limit: int = 10) -> list:
    for instance in NITTER_INSTANCES:
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

def calculate_war_index(per_account_limit: int = 10) -> tuple:
    total_score = 0
    signals = []
    seen_titles = []

    for account in INTEL_ACCOUNTS:
        items = fetch_account(account, limit=per_account_limit)

        for item in items:
            # Başlık ve açıklamayı birleştir (Bazı tweetler sadece açıklamada olur)
            full_text = f"{item.get('title', '')} {item.get('description', '')}"
            
            pts, hits = score_title(full_text)
            if pts == 0: continue

            # Kopya Kontrolü
            dup, dup_idx = is_duplicate(full_text, seen_titles)
            if dup:
                if 0 <= dup_idx < len(signals):
                    asb = signals[dup_idx].setdefault("also_shared_by", [])
                    if account not in asb: asb.append(account)
                continue

            # Zaman damgasını işle
            try:
                dt = parsedate_to_datetime(item.get("pubDate", ""))
                ts = dt.timestamp()
            except: ts = 0

            seen_titles.append(full_text)
            total_score += pts
            
            signals.append({
                "account": account,
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

    SCORE_CEILING = 700
    bar_pct = min(int(total_score / SCORE_CEILING * 100), 100)
    
    for s in signals:
        s["raw_total"] = total_score

    print(f"\n[GATTO] Tarama Bitti | Skor: {total_score} | Bar: %{bar_pct} | Sinyal: {len(signals)}\n")
    return bar_pct, signals


# ─────────────────────────────────────────────────────────────────────────────
# BOT İÇİN RAW VERİ ÇEKİCİ (POSTER_BOT İÇİN GEREKLİ)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_raw_posts(limit_per_account: int = 10) -> list:
    """Tüm hesaplardan ham gönderileri toplar ve bir liste olarak döner."""
    all_raw_posts = []
    print(f"[SCRAPER] {len(INTEL_ACCOUNTS)} hesaptan ham veriler toplanıyor...")
    
    for account in INTEL_ACCOUNTS:
        items = fetch_account(account, limit=limit_per_account)
        for item in items:
            # Botun tanıması için hesap bilgisini ekliyoruz
            item["account"] = account
            all_raw_posts.append(item)
            
    return all_raw_posts