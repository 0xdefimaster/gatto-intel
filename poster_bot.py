import os
import json
import time
import re
import sys
from datetime import datetime, timezone
import google.generativeai as genai
from deep_translator import GoogleTranslator

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
RISK_THRESHOLD      = 10  # Sadece bu puanın üzerindekiler (11, 12...) paylaşılır

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

# ── GEMİNİ YAPILANDIRMASI (RESMİ ÖRNEK FORMAT) ────────────────────────────────
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

if GEMINI_KEY:
    try:
        # Configure API key
        genai.configure(api_key=GEMINI_KEY)
        
        # Generation configuration (Official example format)
        generation_config = {
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 500,
            "response_mime_type": "text/plain",
        }
        
        # Safety settings (Optional - prevents blocking)
        safety_settings = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE",
            },
        ]
        
        # Model initialization (Official example format)
        ai_model = genai.GenerativeModel(
            model_name="gemini-3-flash-preview",  # veya "gemini-1.5-pro" daha iyi sonuç için
            generation_config=generation_config,
            safety_settings=safety_settings,
        )
        
        print("[✓] Gemini AI başarıyla yapılandırıldı (gemini-3-flash-preview).")
        
    except Exception as e:
        print(f"[✗] Gemini yapılandırma hatası: {e}")
        ai_model = None
else:
    print("[!] GEMINI_API_KEY bulunamadı, AI devre dışı (Çifte çeviri aktif).")
    ai_model = None

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

# ── ÖZGÜNLEŞTİRME MOTORU ──────────────────────────────────────────────────────

def smart_rewrite(text: str) -> str:
    """
    Plan A: Gemini ile profesyonelce yeniden yazar (Official API format).
    Plan B: Hata durumunda çeviri hilesiyle (EN->DE->EN) özgünleştirir.
    Her iki plan da önce clean_raw_text() ile temizlenmiş metin alır.
    """
    # Metni her iki plan için de önce temizle
    text = clean_raw_text(text)

    # ── PLAN A: GEMİNİ (RESMİ ÖRNEK FORMAT) ──────────────────────────────────
    if ai_model:
        try:
            prompt = f"""Rewrite the following intelligence report as ONE concise, professional breaking news paragraph in English.

Instructions:
- Sound like Reuters or AP wire service
- Remove ALL social media handles, "RT", and source attributions
- Do NOT repeat any sentence or phrase
- Complete truncated sentences based on context
- Output ONLY the rewritten text, no explanations

Raw Data: {text}
"""
            # Generate content (Official API format)
            response = ai_model.generate_content(prompt)
            
            # Check if response was blocked
            if not response.text:
                print(f"[!] Gemini: Response blocked or empty, using Plan B")
                raise Exception("Empty response from Gemini")
            
            rewritten = response.text.strip()
            print(f"[✓] Gemini rewrite başarılı ({len(rewritten)} karakter)")
            return rewritten
            
        except Exception as e:
            print(f"[!] Gemini hatası: {e}")
            print(f"[→] Plan B aktif (Çifte çeviri)")

    # ── PLAN B: ÇİFTE ÇEVİRİ (Özgünleştirme Hilesi) ─────────────────────────
    try:
        print("[→] Çifte çeviri başlıyor (EN→DE→EN)...")
        
        # 1. Adım: Metni (TR veya EN fark etmez) Almancaya çevir
        german_text = GoogleTranslator(source='auto', target='de').translate(text)
        
        # 2. Adım: Almancayı tekrar İngilizceye çevir → bu özgünleştirir
        final_english_text = GoogleTranslator(source='de', target='en').translate(german_text)
        
        # Plan B çıktısından da tekrar cümle temizliği yap
        final_english_text = clean_raw_text(final_english_text)
        
        print(f"[✓] Çifte çeviri başarılı ({len(final_english_text)} karakter)")
        return final_english_text
        
    except Exception as e2:
        print(f"[✗] Çeviri motoru hatası: {e2}")
        print(f"[→] Ham metin döndürülüyor (temizlenmiş)")
        return text  # Tüm sistemler çökerse temizlenmiş orijinali döndür

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
    # İçerik tabanlı ID oluşturma (Farklı kaynaklardan gelen aynı haberi engeller)
    content = (post.get("title", "") + post.get("description", ""))[:100]
    return str(abs(hash(content)))

def format_tweet(rewritten_content: str, score: int) -> str:
    """Anonim format: Hesap adı yok, link yok, RT yok."""
    alert_type = "🚨 Critical" if score >= 10 else "🟠 Important"
    header = f"{alert_type} (Risk score: {score})\n\n"

    # Karakter Sınırı Kontrolü
    max_len = 600 - len(header) - 5
    if len(rewritten_content) > max_len:
        rewritten_content = rewritten_content[:max_len] + "..."

    return f"{header}{rewritten_content}"

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

    client = get_client()
    posted_ids = load_posted_ids()
    all_posts = fetch_all_raw_posts(limit_per_account=LIMIT_PER_ACCOUNT)

    count = 0
    for post in all_posts:
        # 1. Puanlama
        raw_text = f"{post.get('title', '')} {post.get('description', '')}"
        score, hits = score_title(raw_text)

        # 2. Risk Puanı Filtresi: RISK_THRESHOLD üzerindeyse devam et
        if score <= RISK_THRESHOLD:
            continue

        # 3. Daha önce paylaşıldı mı?
        pid = make_post_id(post)
        if pid in posted_ids:
            continue

        # 4. Kaynak Bilgisi (Terminale Bas)
        source_account = post.get("account", "Bilinmeyen")
        source_link = post.get("link", "Link yok")
        print(f"[!] Risk tespit edildi | Kaynak: @{source_account} | Link: {source_link} | Score: {score} | Eşleşen: {hits}")

        # 5. Temizle + AI/Çeviri ile Yeniden Yaz
        unique_text = smart_rewrite(raw_text)
        tweet_text = format_tweet(unique_text, score)

        try:
            if client:
                client.create_tweet(text=tweet_text)
                print(f"[✓] Tweet paylaşıldı | Kaynak: @{source_account}")
            else:
                print(f"[DRY RUN]:\n{tweet_text}\n")

            save_posted_id(pid, posted_ids)
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
        # Her 10 dakikada bir çalıştır
        scheduler.add_job(run_cycle, 'interval', minutes=10, next_run_time=datetime.now())
        scheduler.start()
        print("[BOT] 🔥 GATTO INTEL Aktif! (10 dk aralıkla tarama)")
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
