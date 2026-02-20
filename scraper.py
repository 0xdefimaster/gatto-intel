"""
scraper.py — Gatto Intelligence v3.0
Her hesaptan son 10 gönderi çeker + savaş endeksi hesaplar.
"""

import re
import requests
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

INTEL_ACCOUNTS = [
    "sentdefender", "war_monitor",
    "MonitorX99800", "visionergeo", "IranObserver0",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.cz",
]

IMPACT_KEYWORDS = {
    "missile":   10, "airstrike": 10, "explosion":  7,
    "attack":     5, "strike":     5, "rocket":      5,
    "war":        5, "naval":      3, "combat":      4,
    "bomb":       7, "invasion":   9, "offensive":   4,
    "f-35":       3, "carrier":    3, "warship":     3,
    "deployment": 2, "tanker":     5, "blockade":    4,
    "troops":     4, "military":   4, "drone":       3,
    "iran":       3, "israel":     3, "hormuz":      5,
    "hezbollah":  6, "hamas":      6, "houthi":      7,
    "nuclear":    8, "sanctions":  2, "irgc":        6,
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
    tl = title.lower()
    total, hits = 0, []
    for kw, pts in IMPACT_KEYWORDS.items():
        if kw in tl:
            total += pts
            hits.append(kw)
    return total, hits


# ─────────────────────────────────────────────────────────────────────────────
# RSS ÇEKİCİ — LIMIT PARAMETRESİ EKLENDİ
# ─────────────────────────────────────────────────────────────────────────────

def fetch_rss(url: str, label: str, limit: int = 10) -> list:
    """RSS URL'sinden son `limit` kadar öğeyi çeker."""
    try:
        resp = requests.get(
            url, timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (GattoIntel/3.0)"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"[RSS ✗] {label}: {e}")
        return []

    items = []
    for item in root.findall(".//item"):
        title      = item.findtext("title",   "").strip()
        link       = item.findtext("link",    "").strip()
        pubdate    = item.findtext("pubDate", "").strip()
        # Nitter'da orijinal tweet metni <description> içinde olabilir
        description = item.findtext("description", "").strip()

        if title and len(title) > 8:
            # HTML taglerini description'dan temizle
            desc_clean = re.sub(r'<[^>]+>', '', description).strip()
            items.append({
                "title":       title,
                "link":        link,
                "pubDate":     pubdate,
                "description": desc_clean,
            })

        if len(items) >= limit:  # İlk `limit` öğeyi al
            break

    print(f"[RSS ✓] {label} → {len(items)} öğe (limit={limit})")
    return items


def fetch_account(account: str, limit: int = 10) -> list:
    """
    Nitter → fallback: Google News.
    Her kaynaktan son `limit` gönderiyi döner.
    """
    for instance in NITTER_INSTANCES:
        url     = f"{instance.rstrip('/')}/{account}/rss"
        results = fetch_rss(url, f"nitter/@{account}", limit=limit)
        if results:
            for r in results:
                r["account"] = account
                r["source"]  = "nitter"
            return results

    print(f"[FALLBACK] @{account}: Nitter yok → Google News")
    query   = requests.utils.quote(f'"{account}" twitter')
    url     = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    results = fetch_rss(url, f"gnews/@{account}", limit=limit)
    for r in results:
        r["account"] = account
        r["source"]  = "gnews"
    return results


# ─────────────────────────────────────────────────────────────────────────────
# SAVAŞ ENDEKSİ (app.py tarafından çağrılır)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_war_index(per_account_limit: int = 10) -> tuple:
    """
    Tüm hesapları tara, keyword skorla, dedup uygula.
    Döner: (bar_pct: 0-100, signals: list)
    """
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
# HAM GÖNDERI ÇEKME (poster_bot.py tarafından çağrılır)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_raw_posts(limit_per_account: int = 10) -> list:
    """
    Her hesabın son `limit_per_account` gönderisini çeker.
    Döner: ham post listesi (dedup / skorlama yok)
    Format: [{"account", "title", "description", "link", "pubDate", "source"}, ...]
    """
    all_posts = []
    for account in INTEL_ACCOUNTS:
        posts = fetch_account(account, limit=limit_per_account)
        all_posts.extend(posts)
        print(f"  @{account}: {len(posts)} gönderi toplandı")
    return all_posts
