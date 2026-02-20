"""
poster_bot.py — Gatto Intelligence Repost Botu v2.0
Her paylaşımda:
  - Kaynak hesap: 📰 Source: @hesap
  - Zaman damgası: ⏰ Time: HH:MM UTC | DD Mon YYYY
  - Yasal uyarı metni
"""

import os
import json
import time
import re
import sys
from datetime import datetime, timezone

try:
    import tweepy
    TWEEPY_OK = True
except ImportError:
    TWEEPY_OK = False
    print("[BOT] HATA: pip install tweepy")

from scraper import fetch_all_raw_posts, INTEL_ACCOUNTS

# ── .env yükleyici ─────────────────────────────────────────────────────────────
def load_dotenv(path=".env"):
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
load_dotenv()

API_KEY       = os.environ.get("TWITTER_API_KEY",        "")
API_SECRET    = os.environ.get("TWITTER_API_SECRET",      "")
ACCESS_TOKEN  = os.environ.get("TWITTER_ACCESS_TOKEN",    "")
ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_SECRET",   "")

POSTED_IDS_FILE     = "posted_ids.json"
LIMIT_PER_ACCOUNT   = 10
TWEET_DELAY_SECONDS = 5
MAX_TWEETS_PER_RUN  = 10

# ─────────────────────────────────────────────────────────────────────────────
# SABİT METİNLER
# ─────────────────────────────────────────────────────────────────────────────
DISCLAIMER = (
    "⚠️ Bu hesap yapay zeka tabanlı bir savaş endeksi projesidir (Gatto Intel). "
    "Haberler OSINT kaynaklarından otomatik taranır, "
    "yatırım veya askeri tavsiye içermez."
)

# ─────────────────────────────────────────────────────────────────────────────
# POST ID YÖNETİMİ
# ─────────────────────────────────────────────────────────────────────────────
def load_posted_ids() -> set:
    if not os.path.exists(POSTED_IDS_FILE): return set()
    try:
        with open(POSTED_IDS_FILE) as f:
            return set(json.load(f).get("ids", []))
    except Exception:
        return set()

def save_posted_id(post_id: str, posted_ids: set):
    posted_ids.add(post_id)
    keep = list(posted_ids)[-2000:]
    with open(POSTED_IDS_FILE, "w") as f:
        json.dump({
            "ids": keep,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_saved": len(keep),
        }, f, indent=2)

def make_post_id(post: dict) -> str:
    link = post.get("link", "")
    if link:
        m = re.search(r'/status/(\d+)', link)
        if m: return m.group(1)
        return link
    return str(abs(hash((post.get("title","") + post.get("pubDate",""))[:80])))


# ─────────────────────────────────────────────────────────────────────────────
# ZAMAN FORMATI
# ─────────────────────────────────────────────────────────────────────────────
MONTHS = ["","Jan","Feb","Mar","Apr","May","Jun",
          "Jul","Aug","Sep","Oct","Nov","Dec"]

def format_pubdate(pubdate: str) -> str:
    """
    RSS pubDate → "14:25 UTC | 19 Feb 2026"
    Döner: boş string eğer parse edilemezse
    """
    if not pubdate: return ""
    try:
        from email.utils import parsedate
        p = parsedate(pubdate)
        if p:
            return f"{p[3]:02d}:{p[4]:02d} UTC | {p[2]:02d} {MONTHS[p[1]]} {p[0]}"
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# TWEET METNİ OLUŞTUR
# ─────────────────────────────────────────────────────────────────────────────
def format_tweet(post: dict) -> str:
    """
    Format:
    ─────────────────────────────
    [haber başlığı / tweet metni]

    📰 Source: @sentdefender
    ⏰ Time: 14:25 UTC | 19 Feb 2026
    🔗 [link]

    ⚠️ Bu hesap yapay zeka tabanlı...
    #GATTO $GATTO | theGatto.xyz
    ─────────────────────────────
    Toplam max 280 karakter
    """
    account = post.get("account", "unknown")
    title   = post.get("title", "").strip()
    desc    = post.get("description", "").strip()
    link    = post.get("link", "")
    pubdate = post.get("pubDate", "")

    # İçerik: description daha uzunsa onu kullan (Nitter tam tweet)
    content = desc if (desc and len(desc) > len(title)) else title

    # Zaman satırı
    time_str   = format_pubdate(pubdate)
    time_line  = f"⏰ Time: {time_str}" if time_str else f"⏰ Time: {datetime.now(timezone.utc).strftime('%H:%M UTC | %d %b %Y')}"

    source_line = f"📰 Source: @{account}"
    link_line   = f"🔗 {link}" if link else ""
    tag_line    = "#GATTO $GATTO | theGatto.xyz"

    # ── Her bloğun uzunluğunu hesapla ve içeriği buna göre kes ──
    # Sabit kısım (kaynak + zaman + disclaimer + tag + boşluklar)
    fixed = (
        f"\n\n{source_line}\n"
        f"{time_line}\n"
        f"{link_line}\n\n"
        f"{DISCLAIMER}\n"
        f"{tag_line}"
    )
    max_content = 280 - len(fixed) - 5  # 5 güvenlik payı
    if max_content < 20:
        # Link'i çıkar
        link_line = ""
        fixed = (
            f"\n\n{source_line}\n"
            f"{time_line}\n\n"
            f"{DISCLAIMER}\n"
            f"{tag_line}"
        )
        max_content = 280 - len(fixed) - 5

    if len(content) > max_content:
        content = content[:max_content - 3] + "..."

    tweet = f"{content}\n\n{source_line}\n{time_line}"
    if link_line:
        tweet += f"\n{link_line}"
    tweet += f"\n\n{DISCLAIMER}\n{tag_line}"

    # Güvenlik kırpması
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    return tweet


# ─────────────────────────────────────────────────────────────────────────────
# TWITTER CLIENT
# ─────────────────────────────────────────────────────────────────────────────
def get_client():
    if not TWEEPY_OK: return None
    missing = [k for k, v in {
        "TWITTER_API_KEY":       API_KEY,
        "TWITTER_API_SECRET":    API_SECRET,
        "TWITTER_ACCESS_TOKEN":  ACCESS_TOKEN,
        "TWITTER_ACCESS_SECRET": ACCESS_SECRET,
    }.items() if not v]
    if missing:
        print(f"[BOT] .env'de eksik anahtarlar: {', '.join(missing)}")
        return None
    try:
        return tweepy.Client(
            consumer_key=API_KEY,
            consumer_secret=API_SECRET,
            access_token=ACCESS_TOKEN,
            access_token_secret=ACCESS_SECRET,
            wait_on_rate_limit=True,
        )
    except Exception as e:
        print(f"[BOT] Bağlantı hatası: {e}")
        return None

def post_tweet(client, text: str) -> bool:
    try:
        resp = client.create_tweet(text=text)
        tid  = resp.data["id"]
        print(f"  ✓ https://x.com/i/web/status/{tid}")
        return True
    except tweepy.TweepyException as e:
        print(f"  ✗ Hata: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ANA DÖNGÜ
# ─────────────────────────────────────────────────────────────────────────────
def run(dry_run: bool = False):
    print(f"\n{'═'*60}")
    print(f"  GATTO POSTER BOT v2.0 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {'⚠  DRY RUN — Tweet ATILMAYACAK' if dry_run else '🔴 CANLI MOD'}")
    print(f"{'═'*60}\n")

    posted_ids = load_posted_ids()
    print(f"[BOT] Kayıtlı: {len(posted_ids)} daha önce paylaşılmış gönderi\n")

    print(f"[BOT] {len(INTEL_ACCOUNTS)} hesaptan son {LIMIT_PER_ACCOUNT} gönderi çekiliyor...\n")
    all_posts = fetch_all_raw_posts(limit_per_account=LIMIT_PER_ACCOUNT)
    print(f"\n[BOT] Toplam {len(all_posts)} gönderi çekildi.\n")

    # Yeni gönderileri filtrele
    new_posts = []
    for post in all_posts:
        pid = make_post_id(post)
        if pid not in posted_ids:
            post["_id"] = pid
            new_posts.append(post)

    print(f"[BOT] Yeni gönderi: {len(new_posts)} | Bu çalıştırmada max: {MAX_TWEETS_PER_RUN}\n")

    if not new_posts:
        print("[BOT] Paylaşılacak yeni gönderi yok.")
        return

    client = None
    if not dry_run:
        client = get_client()
        if not client: return

    sent = failed = 0
    print(f"{'─'*60}")

    for post in new_posts[:MAX_TWEETS_PER_RUN]:
        tweet_text = format_tweet(post)
        char_count = len(tweet_text)

        print(f"\n  📡 @{post['account']} | {post.get('pubDate','')[:22]}")
        print(f"  {post['title'][:65]}...")
        print(f"  [{char_count}/280 karakter]")

        if dry_run:
            print(f"\n  ── TWEET METNİ ({'─'*30})")
            for line in tweet_text.split('\n'):
                print(f"  {line}")
            print(f"  {'─'*44}")
            sent += 1
            save_posted_id(post["_id"], posted_ids)
            continue

        success = post_tweet(client, tweet_text)
        if success:
            sent += 1
            save_posted_id(post["_id"], posted_ids)
            time.sleep(TWEET_DELAY_SECONDS)
        else:
            failed += 1
            if failed >= 3:
                print("\n[BOT] 3 ardışık hata — durduruluyor.")
                break

    print(f"\n{'─'*60}")
    print(f"[BOT] Özet:")
    print(f"  ✓ Paylaşılan  : {sent}")
    print(f"  ✗ Başarısız   : {failed}")
    skipped = max(0, len(new_posts) - MAX_TWEETS_PER_RUN)
    if skipped:
        print(f"  ○ Sonraki tur : {skipped} gönderi bekliyor")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "-d" in sys.argv
    run(dry_run=dry)
