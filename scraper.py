"""
scraper.py — Gatto Intelligence v3.1
Google News fallback kaldırıldı.
Nitter çalışmazsa sessizce atlar.
"""

import re
import requests
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

INTEL_ACCOUNTS = [
    "sentdefender", "war_monitor",
    "MonitorX99800", "visionergeo", "IranObserver0",
]

NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.kavin.rocks",      # YENİ
    "https://nitter.1d4.us",            # YENİ
    "https://nitter.unixfox.eu",        # YENİ
    "https://nitter.net",               # YENİ
]

IMPACT_KEYWORDS = {
    "missile":   10, "airstrike": 10, "explosion":  7,
    "attack":     5, "strike":     5, "rocket":      5,
    "war":        5, "U.S. Air":      3, "combat":      4,
    "bomb":       7, "Hezbollah":   9, "offensive":   4,
    "f-35":       3, "carrier":    3, "warship":     3,
    "deployment": 2, "tanker":     5, "blockade":    4,
    "troops":     4, "military":   4, "drone":       3,
    "iran":       3, "israel":     3, "hormuz":      5,
    "hezbollah":  6, "hamas":      6, "houthi":      7,
    "nuclear":    8, "su-35":  2, "galaxy":        6,
}

# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI
# ─────────────────────────────────────────────────────────────────────────────

def normalize(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r'\s[-|–]\s.*$', '', t)
    t = re.sub(r'[^\w\s]', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def is_duplicate(new_title: str, seen: list, threshold: float = 0.82) -> tuple:
    norm_new  = normalize(new_title)
    words_new = set(norm_new.split())
    for i, old in enumerate(seen):
        norm_old  = normalize(old)
        words_old = set(norm_old.split())
        if norm_new == norm_old:
            return True, i
        union = words_new | words_old
        if union and len(words_new & words_old) / len(union) >= 0.62:
            return True, i
        if SequenceMatcher(None, norm_new, norm_old).ratio() >= threshold:
            return True, i
    return False, -1


def score_title(title: str) -> tuple:
    # Metni küçült ama özel karakterleri (tire gibi) koru
    tl = title.lower() 
    total, hits = 0, []
    for kw, pts in IMPACT_KEYWORDS.items():
        # Kelime bazlı tam eşleşme veya metin içinde geçiş kontrolü
        if kw in tl:
            total += pts
            hits.append(kw)
    return total, hits


# ─────────────────────────────────────────────────────────────────────────────
# RSS ÇEKİCİ
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rss(url: str, label: str, limit: int = 10) -> list:
    try:
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (GattoIntel/3.1)"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[RSS ✗] {label}: {e}")
        return []

    items = []
    for item in root.findall(".//item"):
        title       = item.findtext("title",       "").strip()
        link        = item.findtext("link",        "").strip()
        pubdate     = item.findtext("pubDate",     "").strip()
        description = item.findtext("description", "").strip()

        if title and len(title) > 8:
            desc_clean = re.sub(r'<[^>]+>', '', description).strip()
            items.append({
                "title":       title,
                "link":        link,
                "pubDate":     pubdate,
                "description": desc_clean,
            })

        if len(items) >= limit:
            break

    print(f"[RSS ✓] {label} → {len(items)} öğe (limit={limit})")
    return items


def fetch_account(account: str, limit: int = 10) -> list:
    """
    Sadece Nitter dener. Hiçbiri çalışmazsa boş döner.
    Google News fallback YOK.
    """
    for instance in NITTER_INSTANCES:
        url     = f"{instance.rstrip('/')}/{account}/rss"
        results = fetch_rss(url, f"nitter/@{account}", limit=limit)
        if results:
            for r in results:
                r["account"] = account
                r["source"]  = "nitter"
            return results

    print(f"[SKIP] @{account}: Tüm Nitter instance'ları erişilemez, atlanıyor.")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# SAVAŞ ENDEKSİ
# ─────────────────────────────────────────────────────────────────────────────

def calculate_war_index(per_account_limit: int = 10) -> tuple:
    total_score = 0
    signals     = []
    seen_titles = []

    for account in INTEL_ACCOUNTS:
        # Nitter'dan verileri çek
        items = fetch_account(account, limit=per_account_limit)

        for item in items:
            # 1. BAŞLIK VE AÇIKLAMAYI BİRLEŞTİR (Daha iyi tarama için)
            full_text = f"{item.get('title', '')} {item.get('description', '')}"
            
            # 2. PUANLAMA
            pts, hits = score_title(full_text)
            
            if pts == 0:
                continue

            # 3. TARİHİ İŞLE (Sıralama için sayıya çeviriyoruz)
            try:
                dt = parsedate_to_datetime(item.get("pubDate", ""))
                timestamp = dt.timestamp()
            except Exception:
                timestamp = 0

            # 4. KOPYA KONTROLÜ
            dup, dup_idx = is_duplicate(full_text, seen_titles)
            
            if dup:
                if 0 <= dup_idx < len(signals):
                    asb = signals[dup_idx].setdefault("also_shared_by", [])
                    if account not in asb:
                        asb.append(account)
                continue

            # 5. SİNYAL LİSTESİNE EKLE
            seen_titles.append(full_text)
            total_score += pts
            
            # Ekranda görünecek temiz metin
            display_text = item.get('title', '') if len(item.get('title', '')) > 30 else full_text
            
            signals.append({
                "account":        account,
                "text":           display_text[:160].replace('\n', ' ').strip() + "...",
                "title":          item.get('title', '')[:120],
                "link":           item.get("link", ""),
                "pubDate":        item.get("pubDate", ""),
                "timestamp":      timestamp, # Sıralama anahtarı
                "score":          pts,
                "keywords":       hits,
                "also_shared_by": [],
            })

    # --- KRİTİK SIRALAMA ---
    # Artik 'score' yerine 'timestamp' kullanıyoruz. 
    # Böylece en tehlikeli olan değil, EN SON PAYLAŞILAN en üstte çıkar.
    signals.sort(key=lambda x: x["timestamp"], reverse=True)

    # Endeks hesaplama (Aynı kalıyor)
    SCORE_CEILING = 400
    bar_pct = min(int(total_score / SCORE_CEILING * 100), 100)
    
    for s in signals:
        s["raw_total"] = total_score

    print(f"\n[GATTO] Tarama Tamamlandı | Toplam Skor: {total_score} | Bar: %{bar_pct} | Sinyal: {len(signals)}\n")
    return bar_pct, signals
    total_score = 0
    signals     = []
    seen_titles = []

    for account in INTEL_ACCOUNTS:
        # Limit değerini biraz artırmak (örn: 15) kaçırma ihtimalini düşürür
        items = fetch_account(account, limit=per_account_limit)

        for item in items:
            # 1. BAŞLIK VE AÇIKLAMAYI BİRLEŞTİR (En Kritik Güncelleme)
            # Bazı Nitter instance'ları tweet metnini sadece description'a koyar.
            full_text = f"{item.get('title', '')} {item.get('description', '')}"
            
            # 2. PUANLAMA (Birleşik metin üzerinden)
            pts, hits = score_title(full_text)
            
            if pts == 0:
                continue

            # 3. KOPYA KONTROLÜ
            # Duplicate kontrolünü de genişletilmiş metin üzerinden yapıyoruz
            dup, dup_idx = is_duplicate(full_text, seen_titles)
            
            if dup:
                if 0 <= dup_idx < len(signals):
                    asb = signals[dup_idx].setdefault("also_shared_by", [])
                    if account not in asb:
                        asb.append(account)
                # print(f"  [DEDUP] @{account}: Tekrar eden içerik atlandı.")
                continue

            # 4. SİNYAL LİSTESİNE EKLE
            seen_titles.append(full_text)
            total_score += pts
            
            # Tweetin en temiz halini 'text' olarak sakla (Görsel kirliliği önlemek için ilk 160 karakter)
            display_text = item.get('title', '') if len(item.get('title', '')) > 30 else full_text
            
            signals.append({
                "account":        account,
                "text":           display_text[:160].replace('\n', ' ').strip() + "...",
                "title":          item.get('title', '')[:120],
                "link":           item.get("link", ""),
                "pubDate":        item.get("pubDate", ""),
                "score":          pts,
                "keywords":       hits,
                "also_shared_by": [],
            })

    # Skorlara göre sırala
    signals.sort(key=lambda x: x["score"], reverse=True)

    SCORE_CEILING = 400
    bar_pct   = min(int(total_score / SCORE_CEILING * 100), 100)
    
    # Tüm sinyallere o anki toplam skoru yaz (frontend için)
    for s in signals:
        s["raw_total"] = total_score

    print(f"\n[GATTO] Tarama Tamamlandı | Skor: {total_score} | Bar: %{bar_pct} | Aktif Sinyal: {len(signals)}\n")
    return bar_pct, signals
    total_score = 0
    signals     = []
    seen_titles = []

    for account in INTEL_ACCOUNTS:
        items = fetch_account(account, limit=per_account_limit)

        for item in items:
            pts, hits = score_title(item["title"])
            if pts == 0:
                continue

            dup, dup_idx = is_duplicate(item["title"], seen_titles)
            if dup:
                if 0 <= dup_idx < len(signals):
                    asb = signals[dup_idx].setdefault("also_shared_by", [])
                    if account not in asb:
                        asb.append(account)
                print(f"  [DEDUP] @{account}: «{item['title'][:55]}...»")
                continue

            seen_titles.append(item["title"])
            total_score += pts
            signals.append({
                "account":        account,
                "text":           item["title"][:120],
                "title":          item["title"][:120],
                "link":           item.get("link", ""),
                "pubDate":        item.get("pubDate", ""),
                "score":          pts,
                "keywords":       hits,
                "also_shared_by": [],
            })

    signals.sort(key=lambda x: x["score"], reverse=True)

    SCORE_CEILING = 400
    bar_pct   = min(int(total_score / SCORE_CEILING * 100), 100)
    raw_score = total_score

    for s in signals:
        s["raw_total"] = raw_score

    print(f"\n[GATTO] Ham Skor: {raw_score} | Bar: %{bar_pct} | Sinyal: {len(signals)}\n")
    return bar_pct, signals


# ─────────────────────────────────────────────────────────────────────────────
# HAM GÖNDERI ÇEKME
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_raw_posts(limit_per_account: int = 10) -> list:
    all_posts = []
    for account in INTEL_ACCOUNTS:
        posts = fetch_account(account, limit=limit_per_account)
        all_posts.extend(posts)
        print(f"  @{account}: {len(posts)} gönderi toplandı")
    return all_posts
