import os
import json
import time
import re
import sys
from datetime import datetime, timezone
import google.generativeai as genai
from deep_translator import GoogleTranslator
import hashlib


# ── KÜTÜPHANE KONTROLLERİ ─────────────────────────────────────────────────────
try:
    import google.generativeai as genai
    from deep_translator import GoogleTranslator
except ImportError:
    print("[BOT] HATA: 'pip install google-generativeai deep-translator' eksik!")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    APS_OK = True
except ImportError:
    APS_OK = False

try:
    import tweepy
    TWEEPY_OK = True
except ImportError:
    TWEEPY_OK = False

try:
    from scraper import fetch_all_raw_posts, score_title
except ImportError:
    print("[BOT] HATA: scraper.py bulunamadı!")
    sys.exit(1)

# ── AYARLAR & ANAHTARLAR ──────────────────────────────────────────────────────
LIMIT_PER_ACCOUNT   = 5
TWEET_DELAY_SECONDS = 30
MAX_TWEETS_PER_RUN  = 5
POSTED_IDS_FILE     = "posted_ids.json"
RISK_THRESHOLD      = 9  # Sadece bu puanın üzerindekiler paylaşılır

# Gemini model sıralaması — rate limit'e göre sırayla denenir
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite-preview-06-17",
    "gemini-3-flash-preview",
]

# .env veya Ortam Değişkenlerini Yükle
def load_dotenv(path=".env"):
    if not os.path.exists(path): return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_dotenv()

# API Anahtarları
API_KEY       = os.environ.get("TWITTER_API_KEY", "")
API_SECRET    = os.environ.get("TWITTER_API_SECRET", "")
ACCESS_TOKEN  = os.environ.get("TWITTER_ACCESS_TOKEN", "")
ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_SECRET", "")
GEMINI_KEY    = os.environ.get("GEMINI_API_KEY", "")

# ── GEMİNİ YAPILANDIRMASI ─────────────────────────────────────────────────────
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        print(f"[✓] Gemini API yapılandırıldı. Model sırası: {' → '.join(GEMINI_MODELS)} → Plan B")
    except Exception as e:
        print(f"[✗] Gemini yapılandırma hatası: {e}")
else:
    print("[!] GEMINI_API_KEY bulunamadı, AI devre dışı (Çifte çeviri aktif).")

# ── TEMİZLEME FONKSİYONU ──────────────────────────────────────────────────────

def clean_raw_text(text: str) -> str:
    """
    Ham metni Gemini veya çeviri motoruna göndermeden önce temizler:
    - 'RT from/by @kullanici:' kalıplarını siler
    - Tekil @mention'ları siler
    - Tekrar eden cümleleri kaldırır (sırayı koruyarak)
    """
    # "RT from @kullanici:" veya "RT by @kullanici:" kalıplarını sil
    text = re.sub(r'RT\s+(from|by)\s+@\w+\s*:?\s*', '', text, flags=re.IGNORECASE)
    # Genel "RT" kalıntısını sil
    text = re.sub(r'\bRT\b\s*', '', text, flags=re.IGNORECASE)
    # Tekil @mention'ları sil
    text = re.sub(r'@\w+', '', text)
    # Fazla boşlukları temizle
    text = re.sub(r'\s{2,}', ' ', text).strip()
    # Cümleleri ayır, tekrar edenleri çıkar (sırayı koru)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    seen = []
    for s in sentences:
        if s.strip() and s.strip().lower() not in [x.lower() for x in seen]:
            seen.append(s.strip())
    return ' '.join(seen).strip()

# ── BENZERLİK TEMİZLEME FONKSİYONU ───────────────────────────────────────────

def remove_similar_sentences(text: str) -> str:
    """
    Kelime örtüşmesi %60'ın üzerinde olan tekrar eden cümleleri kaldırır.
    Haber kesilmez, sadece gerçek tekrarlar çıkarılır.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    filtered = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        s_words = set(s.lower().split())
        is_duplicate = False
        for kept in filtered:
            kept_words = set(kept.lower().split())
            if len(s_words) > 0 and len(kept_words) > 0:
                overlap = len(s_words & kept_words) / max(len(s_words), len(kept_words))
                if overlap > 0.6:
                    is_duplicate = True
                    break
        if not is_duplicate:
            filtered.append(s)
    return ' '.join(filtered).strip()

# ── ÖZGÜNLEŞTİRME MOTORU ──────────────────────────────────────────────────────

def smart_rewrite(text: str) -> str:
    """
    Plan A: gemini-2.5-flash → gemini-2.5-flash-lite → Plan B (çifte çeviri)
    Rate limit'e takılınca sıradaki modele geçer, hepsi başarısız olursa Plan B.
    """
    text = clean_raw_text(text)

    # Sırayla denenecek Gemini modelleri
    GEMINI_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite-preview-06-17",
        "gemini-3-flash-preview",
    ]

    prompt = f"""Rewrite the following intelligence report as ONE concise, professional breaking news paragraph in English.

Instructions:
- Sound like Reuters or AP wire service
- Remove ALL social media handles, "RT", and source attributions
- Do NOT repeat any sentence or phrase
- Complete truncated sentences based on context
- Output ONLY the rewritten text, no explanations

Raw Data: {text}
"""

    generation_config = {
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 1000,
        "response_mime_type": "text/plain",
    }

    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    # ── PLAN A: Gemini modelleri sırayla dene ─────────────────────────────────
    if GEMINI_KEY:
        for model_name in GEMINI_MODELS:
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config=generation_config,
                    safety_settings=safety_settings,
                )
                response = model.generate_content(prompt)

                if not response.text:
                    raise Exception("Empty response")

                rewritten = response.text.strip()
                print(f"[✓] {model_name} rewrite başarılı ({len(rewritten)} karakter)")
                return rewritten

            except Exception as e:
                err = str(e)
                if "429" in err or "quota" in err.lower() or "rate" in err.lower() or "resource_exhausted" in err.lower():
                    print(f"[!] {model_name} rate limit — sonraki model deneniyor...")
                    continue
                else:
                    print(f"[!] {model_name} hatası: {e} — sonraki model deneniyor...")
                    continue

        print("[→] Tüm Gemini modelleri başarısız — Plan B aktif (Çifte çeviri)")

    # ── PLAN B: ÇİFTE ÇEVİRİ (Özgünleştirme Hilesi) ─────────────────────────
    try:
        print("[→] Çifte çeviri başlıyor (EN→DE→EN)...")

        # 1. Adım: Metni (TR veya EN fark etmez) Almancaya çevir
        german_text = GoogleTranslator(source='auto', target='de').translate(text)

        # 2. Adım: Almancayı tekrar İngilizceye çevir → bu özgünleştirir
        final_english_text = GoogleTranslator(source='de', target='en').translate(german_text)

        # Plan B çıktısından tekrar cümle temizliği yap
        final_english_text = clean_raw_text(final_english_text)

        # Benzer cümleleri de temizle (kelime örtüşmesi %60'ın üzerindeyse sil)
        final_english_text = remove_similar_sentences(final_english_text)

        print(f"[✓] Çifte çeviri başarılı ({len(final_english_text)} karakter)")
        return final_english_text

    except Exception as e2:
        print(f"[✗] Çeviri motoru hatası: {e2}")
        print(f"[→] Ham metin döndürülüyor (temizlenmiş)")
        return text

# ── YARDIMCI FONKSİYONLAR ─────────────────────────────────────────────────────

def load_posted_ids() -> set:
    if not os.path.exists(POSTED_IDS_FILE): return set()
    try:
        with open(POSTED_IDS_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("ids", []))
    except Exception: return set()

def save_posted_id(post_id: str, posted_ids: set):
    posted_ids.add(post_id)
    keep = list(posted_ids)[-2000:]
    with open(POSTED_IDS_FILE, "w") as f:
        json.dump({"ids": keep, "last_updated": datetime.now(timezone.utc).isoformat()}, f, indent=2)

def make_post_id(post: dict) -> str:
    # Sadece title'ın ilk 50 karakteri — farklı kaynaklardan gelen aynı haber için aynı ID
    content = post.get("title", "")[:50].lower().strip()
    return hashlib.md5(content.encode()).hexdigest()

def format_tweet(rewritten_content: str, score: int) -> str:
    """Tweet formatı: Başlık + Haber + Dinamik Hashtagler"""
    alert_type = "🚨 Critical" if score >= 10 else "🟠 Important"
    header = f"{alert_type} (Risk score: {score})\n\n"

    # İçeriğe göre dinamik hashtagler
    text_lower = rewritten_content.lower()
    tags = []

    if any(w in text_lower for w in ["ukraine", "kiev", "kyiv", "zelensky"]):
        tags.append("#Ukraine")
    if any(w in text_lower for w in ["russia", "putin", "moscow", "kremlin"]):
        tags.append("#Russia")
    if any(w in text_lower for w in ["missile", "rocket", "strike", "attack"]):
        tags.append("#WarUpdate")
    if any(w in text_lower for w in ["nato", "eu ", "europe", "european"]):
        tags.append("#NATO")
    if any(w in text_lower for w in ["israel", "gaza", "hamas", "idf"]):
        tags.append("#MiddleEast")
    if any(w in text_lower for w in ["iran", "tehran"]):
        tags.append("#Iran")
    if any(w in text_lower for w in ["china", "beijing", "taiwan"]):
        tags.append("#China")
    if any(w in text_lower for w in ["weapon", "ammo", "ammunition", "defense", "military"]):
        tags.append("#Defense")

    tags.append("#BreakingNews")
    tags.append("#Intel")

    hashtags = "\n\n" + " ".join(tags[:6])

    max_len = 4000 - len(header) - len(hashtags) - 10
    if len(rewritten_content) > max_len:
        rewritten_content = rewritten_content[:max_len] + "..."

    return f"{header}{rewritten_content}{hashtags}"

def get_client():
    if not TWEEPY_OK: return None
    if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET]):
        print("[BOT] Twitter anahtarları eksik!")
        return None
    try:
        return tweepy.Client(
            consumer_key=API_KEY, consumer_secret=API_SECRET,
            access_token=ACCESS_TOKEN, access_token_secret=ACCESS_SECRET
        )
    except Exception as e:
        print(f"[BOT] Bağlantı hatası: {e}")
        return None

# ── ANA ÇALIŞTIRICI ──────────────────────────────────────────────────────────

def run_cycle():
    print(f"\n[SCAN] Tarama başladı: {datetime.now().strftime('%H:%M:%S')}")

    client        = get_client()
    posted_ids    = load_posted_ids()
    all_posts     = fetch_all_raw_posts(limit_per_account=LIMIT_PER_ACCOUNT)
    count         = 0
    recent_titles = []  # Bu döngüde işlenen başlıklar (duplicate kontrolü)

    for post in all_posts:
        # 1. Puanlama
        raw_text = f"{post.get('title', '')} {post.get('description', '')}"
        score, hits = score_title(raw_text)

        # 2. Risk Puanı Filtresi
        if score <= RISK_THRESHOLD:
            continue

        # 3. Daha önce paylaşıldı mı?
        pid = make_post_id(post)
        if pid in posted_ids:
            continue

        # 4. Bu döngüde benzer başlık işlendi mi?
        title_short = post.get("title", "")[:60].lower().strip()
        if any(title_short in t or t in title_short for t in recent_titles):
            print(f"[~] Duplicate atlandı: {title_short[:40]}...")
            continue
        recent_titles.append(title_short)

        # 5. Kaynak Bilgisi (Terminale Bas)
        source_account = post.get("account", "Bilinmeyen")
        source_link = post.get("link", "Link yok")
        print(f"[!] Risk tespit edildi | Kaynak: @{source_account} | Score: {score}")

        # 6. ID'yi hemen kaydet — çift tweet'i engeller
        save_posted_id(pid, posted_ids)

        # 7. Temizle + AI/Çeviri ile Yeniden Yaz
        unique_text = smart_rewrite(raw_text)

        # 8. Formatla
        tweet_text = format_tweet(unique_text, score)

        try:
            if client:
                client.create_tweet(text=tweet_text)
                print(f"[✓] Tweet paylaşıldı | Premium Mod Aktif")
            else:
                print(f"[DRY RUN]:\n{tweet_text}\n")

            count += 1

            if count >= MAX_TWEETS_PER_RUN:
                print(f"[→] Maksimum tweet sayısına ulaşıldı ({MAX_TWEETS_PER_RUN})")
                break

            time.sleep(TWEET_DELAY_SECONDS)

        except Exception as e:
            print(f"[✗] Tweet hatası: {e}")

    print(f"[✓] Döngü tamamlandı. {count} tweet paylaşıldı.\n")

# ── ZAMANLAYICI KURULUMU ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if APS_OK:
        scheduler = BackgroundScheduler()
        # Her 20 dakikada bir çalıştır
        scheduler.add_job(run_cycle, 'interval', minutes=15, next_run_time=datetime.now())
        scheduler.start()
        print("[BOT] 🔥 GATTO INTEL Aktif! (15 dk aralıkla tarama | Premium Mode)")
        print("[BOT] Ctrl+C ile durdurun.\n")

        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            print("\n[BOT] Kapatılıyor...")
            scheduler.shutdown()
    else:
        print("[BOT] APScheduler yok, tek seferlik çalışıyor...")
        run_cycle()
