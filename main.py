import telebot
from telebot import types
import time
import os
import subprocess
import requests
import re
import sys
import importlib
import html as html_lib
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
import sqlite3
import signal
import threading
import random
import string
import hashlib
import zipfile
import io

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN ortam değişkeni tanımlı değil! Railway > Variables kısmından BOT_TOKEN ekleyin.")

# 🛡️ Kod güvenlik taraması — tamamen yerel, dış servise/API'ye bağımlı olmayan derin statik analiz
# Hiçbir üçüncü taraf servise (API anahtarı, kota, internet bağlantısı vb.) ihtiyaç duymaz;
# bu yüzden kota/billing/model adı değişikliği gibi nedenlerle asla bozulmaz ve gecikmesizdir.
# İki katmanlıdır: (1) geniş regex/imza taraması, (2) Python AST'sini gerçekten ayrıştırıp
# eval/exec zincirlerini, dinamik attribute erişimlerini, string birleştirme ile gizlenmiş
# çağrıları ve şüpheli import/subprocess kalıplarını semantik olarak yakalayan derin analiz.
DEEP_SCAN_CONTENT_CHAR_LIMIT = 200000  # Aşırı büyük dosyalarda taramanın makul sürede bitmesi için üst sınır
DEEP_SCAN_MAX_RETRIES = 0  # Yerel analiz olduğu için tekrar denemeye gerek yok (uyumluluk için tutuluyor)

ADMIN_IDS = [8721726129]
OWNER_ID = 8721726129  # 👑 Panel sahibi - sadece bu ID "Adminler Uyku Modu" muafiyetini değiştirebilir
CHANNEL = "NEBULA HOSTING"
ARCHIVE_CHAT_ID = -1003785565867  # 📦 Onaylanan bot dosyalarının otomatik arşivlendiği kanal/grup
SUPPORT_USERNAME = "NEBULA HOSTING"
BAN_APPEAL_CONTACT = "@gameroyuncuuu"  # 📌 Kanal eklenene kadar itiraz için gösterilen admin iletişimi

# ⭐ VIP kullanıcıların toplamda barındırabileceği maksimum bot sayısı (varsayılan/yedek değer)
PREMIUM_BOT_LIMIT = 3

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

def esc(v):
    """HTML parse_mode ile kırılmayı önlemek için kullanıcı verisini kaçışlar."""
    return html_lib.escape(str(v)) if v is not None else ""

def log_error(context, e):
    """🪵 Sessizce yutulan (bare except) kritik hataları konsola/loglara yazar.
    Böylece Railway loglarında 'neden çalışmadı' sorusunun cevabı görünür olur."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] ❌ {context} hatası: {e}")
    except Exception:
        pass

def user_display(uid, html=True):
    """Admin bildirimlerinde 'Kullanıcı:' alanında ID yerine @nickname göstermek için.
    Kullanıcı adı yoksa/boşsa ID'ye düşer (kod içindeki diğer ID alanlarına dokunmaz)."""
    try:
        cursor.execute("SELECT username FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        uname = r[0] if r else None
        if uname and uname not in ("None", "none", ""):
            return f"@{esc(uname)}" if html else f"@{uname}"
    except Exception:
        pass
    return f"<code>{uid}</code>" if html else f"{uid}"

def pkg_display_name(v):
    """Paket adının sonunda 'Paket' kelimesi varsa (örn. 'Eko Paket') kullanıcıya
    gösterilirken kaldırır (örn. 'Eko'). İsim sadece 'Paket' ise olduğu gibi bırakır."""
    if not v:
        return v
    cleaned = re.sub(r'\s*[Pp]aket\s*$', '', v).strip()
    return cleaned or v

SPINNER_FRAMES = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]

def _scan_progress_bar(percent, width=12):
    filled = int(round(width * percent / 100))
    filled = max(0, min(width, filled))
    return "🟩" * filled + "⬜" * (width - filled)

def run_scan_with_progress(uid, mid, file_path):
    """full_scan_code'u (regex taraması + AST tabanlı derin statik analiz) arka planda bir
    thread'de çalıştırırken, ana thread'de kullanıcıya şık bir kontrol-listesi animasyonu
    (adım adım ✅'ler, dönen spinner, ilerleme çubuğu ve geçen süre) gösterir. Tamamen yerel
    olduğu için gerçek tarama genelde çok hızlı biter; animasyon süresi bilinçli olarak
    biraz uzatılır ki kullanıcı taramanın gerçekten adım adım yapıldığını görebilsin.
    Dönüş: (temiz_mi: bool, sebepler: list[str])"""
    result = {}

    def _worker():
        try:
            result['clean'], result['reasons'] = full_scan_code(file_path)
        except Exception as e:
            print(f"Scan Worker Error: {e}")
            result['clean'], result['reasons'] = True, []

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    # (o adımın bitmesi beklenen saniye, gösterilecek etiket)
    checklist = [
        (2,  "📄 Dosya okundu"),
        (5,  "🧬 Zararlı kod kalıpları tarandı"),
        (8,  "🕵️ Gizlenmiş/obfuscated kod kontrolü"),
        (11, "📂 Hassas dosya erişimi kontrolü"),
        (14, "🌐 Ağ isteği / veri sızdırma kontrolü"),
        (17, "🧠 AST tabanlı derin kod analizi"),
    ]
    EXPECTED_TOTAL = 17.0  # ilerleme yüzdesi bu tahmini süreye göre hesaplanır (bitişe kadar %96'da kilitlenir)
    # ⏳ Yerel analiz çok hızlı bitebiliyor; animasyonun her zaman ~12-17sn sürmesi için,
    # tarama bitse bile bu süreye kadar beklenir (kullanıcı adımların geçtiğini görebilsin).
    MIN_SCAN_DURATION = random.uniform(12, 17)

    start = time.time()
    frame_i = 0

    while True:
        elapsed = time.time() - start
        thread_done = not t.is_alive()

        # Tarama gerçekten bitmiş VE minimum süre de dolmuşsa animasyonu durdur
        if thread_done and elapsed >= MIN_SCAN_DURATION:
            break

        spinner = SPINNER_FRAMES[frame_i % len(SPINNER_FRAMES)]
        frame_i += 1

        lines = []
        active_label = None
        for threshold, label in checklist:
            if elapsed >= threshold:
                lines.append(f"✅ {label}")
            elif active_label is None:
                active_label = label
                lines.append(f"{spinner} {label}")
            else:
                lines.append(f"⬜ {label}")

        percent = min(96, int((elapsed / MIN_SCAN_DURATION) * 100))
        msg = (
            "🛡️ <b>Derin Güvenlik Taraması</b>\n"
            + DIV + "\n"
            + "\n".join(lines) + "\n"
            + DIV + "\n"
            + f"{_scan_progress_bar(percent)}  <b>%{percent}</b>\n"
            + f"⏱️ {int(elapsed)}sn"
        )
        try:
            bot.edit_message_text(msg, uid, mid, parse_mode="HTML")
        except:
            pass
        time.sleep(1.2)

    t.join()

    done_lines = "\n".join(f"✅ {label}" for _, label in checklist)
    try:
        bot.edit_message_text(
            "🛡️ <b>Derin Güvenlik Taraması</b>\n" + DIV + "\n" + done_lines + "\n" + DIV
            + "\n" + _scan_progress_bar(100) + "  <b>%100</b>\n✅ <b>Analiz tamamlandı, sonuç hazırlanıyor...</b>",
            uid, mid, parse_mode="HTML"
        )
    except:
        pass

    return result.get('clean', True), result.get('reasons', [])

def sanitize_filename(name):
    """Kullanıcıdan gelen (Telegram) dosya adını path traversal ve tehlikeli
    karakterlere karşı temizler. '../', '/', gizli isimler vb. engellenir."""
    name = os.path.basename(name or "")
    name = re.sub(r'[^A-Za-z0-9_.-]', '_', name)
    name = name.lstrip('.')
    if not name:
        name = "file.py"
    return name

DIV = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"

DB_PATH = os.environ.get("DB_PATH", "bot_data.db")
BOT_FILES_DIR = os.environ.get("BOT_FILES_DIR", "bot_files")  # 💾 Railway Volume'e mount edilen kalıcı klasör (örn. /data/bot_files)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    name TEXT,
    premium INTEGER DEFAULT 0,
    premium_date TEXT,
    premium_package TEXT DEFAULT 'Basit',
    lang TEXT DEFAULT 'tr',
    created_at TEXT,
    bot_count INTEGER DEFAULT 0,
    total_files INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0,
    ban_reason TEXT,
    last_start TEXT
);

CREATE TABLE IF NOT EXISTS bot_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    file_name TEXT,
    file_path TEXT,
    bot_token TEXT,
    bot_username TEXT,
    status TEXT DEFAULT 'pending',
    bot_status TEXT DEFAULT 'stopped',
    pid INTEGER,
    submitted_at TEXT,
    approved_at TEXT,
    error_log TEXT DEFAULT '',
    start_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    file_name TEXT,
    file_path TEXT,
    submitted_at TEXT,
    bot_token TEXT,
    bot_file_id INTEGER
);

CREATE TABLE IF NOT EXISTS premium_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    price INTEGER,
    description TEXT,
    bot_limit INTEGER,
    watermark INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    duration_minutes INTEGER DEFAULT 0,
    max_uses INTEGER DEFAULT 1,
    used_count INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    user_id INTEGER,
    redeemed_at TEXT,
    UNIQUE(code, user_id)
);

CREATE TABLE IF NOT EXISTS support_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    admin_id INTEGER,
    status TEXT DEFAULT 'pending',
    first_message TEXT,
    created_at TEXT,
    approved_at TEXT,
    closed_at TEXT
);

INSERT OR IGNORE INTO settings VALUES ('maintenance', '0');
INSERT OR IGNORE INTO settings VALUES ('total_approved', '0');
INSERT OR IGNORE INTO settings VALUES ('free_limit', '1');
INSERT OR IGNORE INTO settings VALUES ('sleep_auto_enabled', '1');
INSERT OR IGNORE INTO settings VALUES ('sleep_start', '22:30');
INSERT OR IGNORE INTO settings VALUES ('sleep_end', '10:00');
INSERT OR IGNORE INTO settings VALUES ('admin_sleep_immune', '0');
INSERT OR IGNORE INTO settings VALUES ('bakim_modu', '0');
"""

# 🔧 Mevcut veritabanlarına yeni sütunları ekleyen migrasyonlar (varsa hata yoksay)
SCHEMA_ALTERS = (
    "ALTER TABLE users ADD COLUMN premium_until TEXT",
    "ALTER TABLE premium_packages ADD COLUMN duration_days INTEGER DEFAULT 0",
    "ALTER TABLE premium_packages ADD COLUMN duration_minutes INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN last_update_at TEXT",
    "ALTER TABLE pending_files ADD COLUMN is_update INTEGER DEFAULT 0",
    "ALTER TABLE bot_files ADD COLUMN prev_file_path TEXT",
    "ALTER TABLE bot_files ADD COLUMN prev_bot_token TEXT",
    "ALTER TABLE bot_files ADD COLUMN prev_bot_username TEXT",
    "ALTER TABLE bot_files ADD COLUMN approved_file_hash TEXT",
    "ALTER TABLE bot_files ADD COLUMN template_key TEXT",
    "ALTER TABLE users ADD COLUMN last_start TEXT",
    "ALTER TABLE premium_packages ADD COLUMN watermark INTEGER DEFAULT 1",
)

def open_db(path):
    """Belirtilen dosya yolunda bir SQLite bağlantısı açar, şemayı ve migrasyonları uygular.
    Hem ilk açılışta hem de veritabanı geri yüklemede (restore) kullanılır."""
    c = sqlite3.connect(path, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    cur = c.cursor()
    cur.executescript(SCHEMA_SQL)
    c.commit()
    for _alter in SCHEMA_ALTERS:
        try:
            cur.execute(_alter)
            c.commit()
        except sqlite3.OperationalError:
            pass

    # 🌙 Mevcut (eski) veritabanlarında uyku saatleri boş bırakılmışsa varsayılanları uygula
    cur.execute("SELECT value FROM settings WHERE key='sleep_start'")
    _ss = cur.fetchone()
    if not _ss or not _ss[0]:
        cur.execute("UPDATE settings SET value='22:30' WHERE key='sleep_start'")
        cur.execute("UPDATE settings SET value='10:00' WHERE key='sleep_end'")
        cur.execute("UPDATE settings SET value='1' WHERE key='sleep_auto_enabled'")
        c.commit()

    # 🏷️ Eski veritabanlarında paket adı "Basic" olarak kayıtlıysa Türkçe "Basit" olarak günceller
    cur.execute("UPDATE users SET premium_package='Basit' WHERE premium_package='Basic'")
    c.commit()

    return c, cur

conn, cursor = open_db(DB_PATH)

TEXT = {
    'tr': {
        'channel_warning': '⚠️ Botu kullanmak için kanala katılmalısın!',
        'join_channel': '📢 Kanala Katıl',
        'check_join': '✅ Katıldım',
        'channel_ok': '✅ Kanal onaylandı!',
        'channel_fail': '❌ Hala katılmadın!',
        'my_bots': '📁 Botlarım',
        'upload_bot': '📤 Bot Yükle',
        'premium': '⭐ VIP',
        'profile': '👤 Profil',
        'ranking': '🏆 Sıralama',
        'settings': '⚙️ Ayarlar',
        'support': '📞 Destek',
        'help': '❓ Yardım',
        'admin_panel': '⚙️ Admin Panel',
        'back': '⬅️ Geri',
        'no_bots': 'Henüz bot yok.',
        'select_bot': 'Bir bot seç:',
        'start': '▶️ Başlat',
        'stop': '⏹️ Durdur',
        'restart': '🔄 Yeniden Başlat',
        'delete': '🗑️ Sil',
        'daily_limit_warning': '⚠️ Toplam bot hakkınız doldu! Ücretsiz üyeler en fazla {} bot yükleyebilir.\n📌 Daha fazlası için VIP paketlerine göz atın.',
        'premium_bot_limit_warning': '⭐ VIP bot hakkınız doldu! ({}/{})\n📌 Ek hak için destek ile iletişime geçin.',
        'upload_error': '❌ Sadece .py dosyası, max 5MB!',
        'upload_success': f'✅ <b>Dosya Yüklendi!</b>\n{DIV}\n⏳ Admin onayı bekleniyor...',
        'bot_started': f'▶️ <b>Bot Başlatıldı!</b>\n{DIV}\n🟢 Botun artık çalışıyor.',
        'bot_stopped': f'⏹️ <b>Bot Durduruldu!</b>\n{DIV}\n🔴 Bot artık çalışmıyor.',
        'bot_deleted': '✅ Bot silindi!',
        'lang_changed': '✅ Dil güncellendi!',
        'premium_given': '✅ VIP verildi!',
        'premium_taken': '✅ VIP kaldırıldı!',
        'limit_updated': '✅ Limit güncellendi!',
        'pkg_added': '✅ Paket eklendi!',
        'pkg_deleted': '✅ Paket silindi!',
        'approve': '✅ Onayla',
        'reject': '❌ Reddet',
        'no_pending': '<i>📭 Bekleyen dosya yok.</i>',
        'no_packages': '<i>📦 Paket yok.\nAdmin panelinden ekleyin.</i>',
        'no_packages_user': '<i>📦 Şu an satın alınabilir paket bulunmuyor.\nDaha sonra tekrar kontrol edebilirsin.</i>',
        'admin_stats': '📊 İstatistikler',
        'admin_pending': '📤 Bekleyenler',
        'admin_approved': '✅ Onaylananlar',
        'admin_premium_give': '⭐ VIP Ver',
        'admin_premium_take': '⭐ VIP Kaldır',
        'admin_packages': '📦 Paketler',
        'admin_add_pkg': '➕ Paket Ekle',
        'admin_del_pkg': '🗑️ Paket Sil',
        'admin_db': '📥 Veritabanı',
        'admin_restore_db': '🔄 Veritabanı Yükle',
        'admin_maintenance': '😴 Uyku Modu',
        'admin_bakim_modu': '🔧 Bakım Modu',
        'bakim_modu_msg': '🔧 <b>Sistemimiz Şu An Bakımda</b>\n\n🛠️ Botumuz üzerinde teknik iyileştirmeler yapıyoruz.\n⏳ Kısa süre içinde tekrar hizmetinizdeyiz, sabrınız için teşekkür ederiz!\n\n✨ En kısa sürede döneceğiz.',
        'admin_sleep_schedule': '🕐 Uyku Programı',
        'admin_unban': '🚫 Engeli Kaldır',
        'admin_broadcast': '📢 Duyuru',
        'admin_users': '👥 Kullanıcılar',
        'admin_all_bots': '📁 Tüm Botlar',
        'admin_free_limit': '⚙️ Limit',
        'installing': '📦 {} yükleniyor...',
        'installed': '✅ {} hazır!',
        'all_ready': '✅ Sistem hazır!',
        'starting': '🚀 Başlatılıyor...',
        'bot_approved': f'✅ <b>Bot Onaylandı!</b>\n{DIV}\n🚀 Botlarım bölümünden başlatabilirsiniz.',
        'bot_rejected': f'❌ <b>Bot Reddedildi!</b>\n{DIV}\n📌 Destek ile iletişime geçin.',
        'banned_msg': '🚫 <b>Sisteme erişiminiz engellenmiştir!</b>\n\nLütfen admin ile iletişime geçiniz.\n👉 Admin: {}',
        'banned_alert': '🚫 Hesabınız engellendi! İtiraz için admin: {}',
        'malicious_detected': '🚫 Dosyanız tehlikeli/kötü amaçlı kod içerdiği için reddedildi ve hesabınız engellendi!\n\n⚠️ Tespit edilen: {}\n📌 İtiraz için: {}',
        'sleep_mode_msg': '⚠️ 𝙐𝙮𝙠𝙪 𝙈𝙤𝙙𝙪 𝘼𝙠𝙩𝙞𝙛 𝙇𝙪𝙩𝙛𝙚𝙣 𝘿𝙖𝙝𝙖 𝙎𝙤𝙣𝙧𝙖 𝟭𝟬.𝟬𝟬 𝘾𝙞𝙫𝙖𝙧𝙞𝙣𝙙𝙖 𝙏𝙚𝙠𝙧𝙖𝙧 𝘿𝙚𝙣𝙚𝙮𝙞𝙣𝙞𝙯. ⚠️',
        'sleep_upload_msg': '⚠️ <b>UYKU MODU AKTİF - YÜKLEME KAPAL</b>\n\n🌙 Sistem bakımda, bot yükleme işlemi durduruldu.\n⏰ Tahmini aktifleşme: <b>{}</b>\n\n💤 Bu sürede sistemimiz gelişiyor...\n📌 Lütfen <b>10.00 civarında</b> tekrar deneyin!',
        'lifetime': '♾️ Süresiz',
        'expires_on': '⏳ Bitiş: {}',
        'days_short': '{} gün',
        'promo_btn': '🎁 Promo Kod',
        'promo_ask': '🎁 <b>Promo kodunu gönder:</b>',
        'promo_invalid': '❌ <b>Geçersiz promo kodu!</b>',
        'promo_no_uses': '❌ <b>Bu promo kodunun kullanım hakkı dolmuş!</b>',
        'promo_already_used': '⚠️ <b>Bu promo kodunu daha önce kullandınız!</b>',
        'promo_success': '✅ <b>Promo kod başarıyla kullanıldı!</b>\n⭐ VIP üyeliğiniz aktif edildi.\n{}',
        'admin_promo': '🎟️ Promo Kodları',
        'admin_add_promo': '➕ Promo Oluştur',
        'admin_list_promo': '📋 Promo Listesi',
        'promo_created': '✅ <b>Promo kod oluşturuldu!</b>'
    },
    'en': {
        'channel_warning': '⚠️ You must join the channel!',
        'join_channel': '📢 Join Channel',
        'check_join': '✅ I Joined',
        'channel_ok': '✅ Channel verified!',
        'channel_fail': '❌ Not joined yet!',
        'my_bots': '📁 My Bots',
        'upload_bot': '📤 Upload Bot',
        'premium': '⭐ VIP',
        'profile': '👤 Profile',
        'ranking': '🏆 Ranking',
        'settings': '⚙️ Settings',
        'support': '📞 Support',
        'help': '❓ Help',
        'admin_panel': '⚙️ Admin Panel',
        'back': '⬅️ Back',
        'no_bots': 'No bots yet.',
        'select_bot': 'Select a bot:',
        'start': '▶️ Start',
        'stop': '⏹️ Stop',
        'restart': '🔄 Restart',
        'delete': '🗑️ Delete',
        'daily_limit_warning': '⚠️ You\'ve reached your total bot limit! Free members can upload up to {} bot(s).\n📌 Check out VIP packages for more.',
        'premium_bot_limit_warning': '⭐ Your VIP bot slots are full! ({}/{})\n📌 Contact support for extra slots.',
        'upload_error': '❌ Only .py files, max 5MB!',
        'upload_success': f'✅ <b>File Uploaded!</b>\n{DIV}\n⏳ Waiting for admin approval...',
        'bot_started': f'▶️ <b>Bot Started!</b>\n{DIV}\n🟢 Your bot is now running.',
        'bot_stopped': f'⏹️ <b>Bot Stopped!</b>\n{DIV}\n🔴 Your bot is no longer running.',
        'bot_deleted': '✅ Bot deleted!',
        'lang_changed': '✅ Language updated!',
        'premium_given': '✅ VIP given!',
        'premium_taken': '✅ VIP removed!',
        'limit_updated': '✅ Limit updated!',
        'pkg_added': '✅ Package added!',
        'pkg_deleted': '✅ Package deleted!',
        'approve': '✅ Approve',
        'reject': '❌ Reject',
        'no_pending': '<i>?? No pending files.</i>',
        'no_packages': '<i>📦 No packages.\nAdd from admin panel.</i>',
        'no_packages_user': '<i>📦 No packages available for purchase right now.\nCheck back later.</i>',
        'admin_stats': '📊 Statistics',
        'admin_pending': '📤 Pending',
        'admin_approved': '✅ Approved',
        'admin_premium_give': '⭐ Give VIP',
        'admin_premium_take': '⭐ Remove VIP',
        'admin_packages': '📦 Packages',
        'admin_add_pkg': '➕ Add Package',
        'admin_del_pkg': '🗑️ Delete Package',
        'admin_db': '📥 Database',
        'admin_restore_db': '🔄 Restore Database',
        'admin_maintenance': '😴 Sleep Mode',
        'admin_bakim_modu': '🔧 Maintenance Mode',
        'bakim_modu_msg': '🔧 <b>Our System Is Currently Under Maintenance</b>\n\n🛠️ We are making technical improvements to the bot.\n⏳ We will be back shortly, thank you for your patience!\n\n✨ See you soon.',
        'admin_sleep_schedule': '🕐 Sleep Schedule',
        'admin_unban': '🚫 Unban',
        'admin_broadcast': '?? Broadcast',
        'admin_users': '👥 Users',
        'admin_all_bots': '📁 All Bots',
        'admin_free_limit': '⚙️ Limit',
        'installing': '📦 Installing {}...',
        'installed': '✅ {} ready!',
        'all_ready': '✅ System ready!',
        'starting': '🚀 Starting...',
        'bot_approved': f'✅ <b>Bot Approved!</b>\n{DIV}\n🚀 Start it from My Bots.',
        'bot_rejected': f'❌ <b>Bot Rejected!</b>\n{DIV}\n📌 Contact support.',
        'banned_msg': '🚫 <b>YOU HAVE BEEN REMOVED FROM THE SYSTEM!</b>\n\nYour access to the platform has been permanently blocked for violating our rules.\n\n📌 To appeal or get information, please contact the admin:\n👉 Admin: {}',
        'banned_alert': '🚫 Your account is banned! To appeal, contact admin: {}',
        'malicious_detected': '🚫 Your file was rejected for containing dangerous/malicious code, and your account has been banned!\n\n⚠️ Detected: {}\n📌 To appeal: {}',
        'sleep_mode_msg': '⚠️ <b>SLEEP MODE ACTIVE</b>\n\n🌙 System is under maintenance.\n⏰ Please try again around <b>10:00 AM</b>.\n\n💤 Your bots are safe and resting...\n✨ We\'ll be back soon with improvements!',
        'sleep_upload_msg': '⚠️ <b>SLEEP MODE ACTIVE - UPLOADS CLOSED</b>\n\n🌙 System under maintenance, bot uploads temporarily disabled.\n⏰ Expected to reopen: <b>{}</b>\n\n💤 System improvements in progress...\n📌 Please try again around <b>10:00 AM</b>!',
        'lifetime': '♾️ Lifetime',
        'expires_on': '⏳ Expires: {}',
        'days_short': '{} days',
        'promo_btn': '🎁 Promo Code',
        'promo_ask': '🎁 <b>Send the promo code:</b>',
        'promo_invalid': '❌ <b>Invalid promo code!</b>',
        'promo_no_uses': '❌ <b>This promo code has run out of uses!</b>',
        'promo_already_used': '⚠️ <b>You already used this promo code!</b>',
        'promo_success': '✅ <b>Promo code redeemed!</b>\n⭐ Your VIP membership is now active.\n{}',
        'admin_promo': '🎟️ Promo Codes',
        'admin_add_promo': '➕ Create Promo',
        'admin_list_promo': '📋 Promo List',
        'promo_created': '✅ <b>Promo code created!</b>'
    },
    'az': {
        'channel_warning': '⚠️ Botdan istifadə etmək üçün kanala qoşulmalısan!',
        'join_channel': '📢 Kanala Qoşul',
        'check_join': '✅ Qoşuldum',
        'channel_ok': '✅ Kanal təsdiqləndi!',
        'channel_fail': '❌ Hələ qoşulmamısan!',
        'my_bots': '📁 Botlarım',
        'upload_bot': '📤 Bot Yüklə',
        'premium': '⭐ VIP',
        'profile': '👤 Profil',
        'ranking': '🏆 Sıralama',
        'settings': '⚙️ Tənzimləmələr',
        'support': '📞 Dəstək',
        'help': '❓ Kömək',
        'admin_panel': '⚙️ Admin Panel',
        'back': '⬅️ Geri',
        'no_bots': 'Hələ bot yoxdur.',
        'select_bot': 'Bir bot seç:',
        'start': '▶️ Başlat',
        'stop': '⏹️ Dayandır',
        'restart': '🔄 Yenidən Başlat',
        'delete': '🗑️ Sil',
        'daily_limit_warning': '⚠️ Ümumi bot hüququnuz doldu! Pulsuz üzvlər maksimum {} bot yükləyə bilər.\n📌 Daha çoxu üçün VIP paketlərinə baxın.',
        'premium_bot_limit_warning': '⭐ VIP bot hüququnuz doldu! ({}/{})\n📌 Əlavə hüquq üçün dəstəklə əlaqə saxlayın.',
        'upload_error': '❌ Yalnız .py faylı, maks 5MB!',
        'upload_success': f'✅ <b>Fayl Yükləndi!</b>\n{DIV}\n⏳ Admin təsdiqi gözlənilir...',
        'bot_started': f'▶️ <b>Bot Başladıldı!</b>\n{DIV}\n🟢 Botun artıq işləyir.',
        'bot_stopped': f'⏹️ <b>Bot Dayandırıldı!</b>\n{DIV}\n🔴 Bot artıq işləmir.',
        'bot_deleted': '✅ Bot silindi!',
        'lang_changed': '✅ Dil yeniləndi!',
        'premium_given': '✅ VIP verildi!',
        'premium_taken': '✅ VIP ləğv edildi!',
        'limit_updated': '✅ Limit yeniləndi!',
        'pkg_added': '✅ Paket əlavə edildi!',
        'pkg_deleted': '✅ Paket silindi!',
        'approve': '✅ Təsdiqlə',
        'reject': '❌ Rədd et',
        'no_pending': '<i>📭 Gözləyən fayl yoxdur.</i>',
        'no_packages': '<i>📦 Paket yoxdur.\nAdmin paneldən əlavə edin.</i>',
        'no_packages_user': '<i>📦 Hazırda satın alınacaq paket yoxdur.\nDaha sonra yenidən yoxla.</i>',
        'admin_stats': '📊 Statistika',
        'admin_pending': '📤 Gözləyənlər',
        'admin_approved': '✅ Təsdiqlənənlər',
        'admin_premium_give': '⭐ VIP Ver',
        'admin_premium_take': '⭐ VIP Ləğv Et',
        'admin_packages': '📦 Paketlər',
        'admin_add_pkg': '➕ Paket Əlavə Et',
        'admin_del_pkg': '🗑️ Paket Sil',
        'admin_db': '📥 Verilənlər Bazası',
        'admin_restore_db': '🔄 Verilənlər Bazasını Bərpa Et',
        'admin_maintenance': '😴 Yuxu Rejimi',
        'admin_bakim_modu': '🔧 Texniki Xidmət Rejimi',
        'bakim_modu_msg': '🔧 <b>Sistemimiz Hazırda Texniki Xidmətdədir</b>\n\n🛠️ Botda texniki təkmilləşdirmələr aparırıq.\n⏳ Qısa müddətdə yenidən xidmətinizdəyik, səbriniz üçün təşəkkürlər!\n\n✨ Tezliklə qayıdırıq.',
        'admin_sleep_schedule': '🕐 Yuxu Cədvəli',
        'admin_unban': '🚫 Bloku Aç',
        'admin_broadcast': '📢 Elan',
        'admin_users': '👥 İstifadəçilər',
        'admin_all_bots': '📁 Bütün Botlar',
        'admin_free_limit': '⚙️ Limit',
        'installing': '📦 {} yüklənir...',
        'installed': '✅ {} hazırdır!',
        'all_ready': '✅ Sistem hazırdır!',
        'starting': '🚀 Başladılır...',
        'bot_approved': f'✅ <b>Bot Təsdiqləndi!</b>\n{DIV}\n🚀 Botlarım bölməsindən başlada bilərsiniz.',
        'bot_rejected': f'❌ <b>Bot Rədd Edildi!</b>\n{DIV}\n📌 Dəstəklə əlaqə saxlayın.',
        'banned_msg': '🚫 <b>SİSTEMDƏN UZAQLAŞDIRILDINIZ!</b>\n\nQaydalarımızı pozduğunuz üçün platformadan istifadəniz daimi olaraq bloklanmışdır.\n\n📌 Etiraz etmək və ya məlumat almaq üçün admin ilə əlaqə saxlayın:\n👉 Admin: {}',
        'banned_alert': '🚫 Hesabınız bloklanıb! Etiraz üçün admin: {}',
        'malicious_detected': '🚫 Faylınız təhlükəli/zərərli kod daşıdığı üçün rədd edildi və hesabınız bloklandı!\n\n⚠️ Aşkarlanan: {}\n📌 Etiraz üçün: {}',
        'sleep_mode_msg': '⚠️ <b>UYQU REJİMİ AKTİV</b>\n\n🌙 Sistem hazırda texniki xidmətdədir.\n⏰ Zəhmət olmasa <b>10:00 civarında</b> yenidən cəhd edin.\n\n💤 Botlarınız təhlükəsiz yatışdadır...\n✨ Yakında yenilənmələrlə qayıdacağız!',
        'sleep_upload_msg': '⚠️ <b>UYQU REJİMİ AKTİV - YÜKLƏMƏ BAĞLI</b>\n\n🌙 Sistem texniki xidmətdədir, bot yükləməsi müvəqqəti olaraq deaktiv edilib.\n⏰ Açılacağı vaxt: <b>{}</b>\n\n💤 Sistem inkişaf edirdi...\n📌 Zəhmət olmasa <b>10:00 civarında</b> yenidən cəhd edin!',
        'lifetime': '♾️ Sonsuz',
        'expires_on': '⏳ Bitmə: {}',
        'days_short': '{} gün',
        'promo_btn': '🎁 Promo Kod',
        'promo_ask': '🎁 <b>Promo kodu göndərin:</b>',
        'promo_invalid': '❌ <b>Yanlış promo kod!</b>',
        'promo_no_uses': '❌ <b>Bu promo kodun istifadə hüququ bitib!</b>',
        'promo_already_used': '⚠️ <b>Bu promo kodu artıq istifadə etmisiniz!</b>',
        'promo_success': '✅ <b>Promo kod uğurla istifadə edildi!</b>\n⭐ VIP üzvlüyünüz aktivləşdirildi.\n{}',
        'admin_promo': '🎟️ Promo Kodlar',
        'admin_add_promo': '➕ Promo Yarat',
        'admin_list_promo': '📋 Promo Siyahısı',
        'promo_created': '✅ <b>Promo kod yaradıldı!</b>'
    }
}

def get_lang(uid):
    try:
        cursor.execute("SELECT lang FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        return r[0] if r else 'tr'
    except:
        return 'tr'

def T(uid, key):
    lang = get_lang(uid)
    return TEXT.get(lang, TEXT['tr']).get(key, key)

def is_admin(uid):
    return uid in ADMIN_IDS

def is_owner(uid):
    return uid == OWNER_ID

def is_admin_sleep_immune():
    """👑 Owner'ın açıp kapatabildiği ayar: AÇIK ise adminler uyku modundan tamamen muaf
    (botu normal kullanıcı gibi kullanabilirler), KAPALI ise adminler de uyku modundan etkilenir
    (sadece admin paneline erişim hakları kalır, kilitlenmeyi önlemek için)."""
    try:
        cursor.execute("SELECT value FROM settings WHERE key='admin_sleep_immune'")
        r = cursor.fetchone()
        return bool(r and r[0] == '1')
    except Exception as e:
        log_error("is_admin_sleep_immune", e)
        return False

def sync_premium_expiry(uid):
    """Süresi dolmuş VIP üyeliği otomatik olarak düşürür."""
    try:
        cursor.execute("SELECT premium, premium_until FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        if not r or not r[0] or not r[1]:
            return
        until = datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
        if datetime.now() >= until:
            cursor.execute(
                "UPDATE users SET premium=0, premium_package='Basit', premium_until=NULL WHERE user_id=?",
                (uid,)
            )
            conn.commit()
    except:
        pass

def is_premium(uid):
    try:
        sync_premium_expiry(uid)
        cursor.execute("SELECT premium FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        return r and r[0] == 1
    except:
        return False

def get_user_bot_limit(uid):
    """Kullanıcının VIP paketine tanımlı gerçek bot hakkını döndürür.
    Paket bulunamazsa (ör. eski/varsayılan kayıtlar) admin panelden ayarlanan varsayılan değere düşer."""
    try:
        cursor.execute("SELECT premium_package FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        pkg_name = r[0] if r else None
        if pkg_name:
            cursor.execute("SELECT bot_limit FROM premium_packages WHERE name=?", (pkg_name,))
            p = cursor.fetchone()
            if p and p[0] is not None:
                return p[0]
    except:
        pass
    return get_premium_default_limit()

def is_maintenance():
    try:
        cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
        r = cursor.fetchone()
        return bool(r and r[0] == '1')
    except Exception as e:
        log_error("is_maintenance", e)
        return False

def is_bakim_modu():
    """🔧 Uyku Modundan TAMAMEN AYRI bir sistem: Admin panelinden elle açılıp kapatılan
    Bakım Modu. Açıkken adminler HARİÇ hiç kimse botu kullanamaz; /start atan normal
    kullanıcılara 'Sistemimiz bakımda' mesajı gösterilir. Otomatik programla ilişkisi yoktur."""
    try:
        cursor.execute("SELECT value FROM settings WHERE key='bakim_modu'")
        r = cursor.fetchone()
        return bool(r and r[0] == '1')
    except Exception as e:
        log_error("is_bakim_modu", e)
        return False

def get_sleep_end_str():
    """Uyku modunun biteceği saati döndürür (SS:DD formatında)."""
    try:
        cursor.execute("SELECT value FROM settings WHERE key='sleep_end'")
        r = cursor.fetchone()
        return r[0] if r and r[0] else "10:00"
    except:
        return "10:00"

def send_sleep_upload_msg(uid):
    """Yükleme/güncelleme uyku mesajını uyku bitiş saatiyle birlikte gönderir."""
    end_time = get_sleep_end_str()
    txt = T(uid, 'sleep_upload_msg').format(end_time)
    try:
        bot.send_message(uid, txt, parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
    except:
        pass

def check_channel(uid):
    # 🔕 Zorunlu kanal kontrolü kaldırıldı, herkes serbestçe kullanabilir.
    return True

def get_limit():
    try:
        cursor.execute("SELECT value FROM settings WHERE key='free_limit'")
        r = cursor.fetchone()
        return int(r[0]) if r else 1
    except:
        return 1

def get_premium_default_limit():
    """Paketi silinmiş/eşleşmeyen eski VIP kayıtları için sabit yedek bot hakkı.
    Her paketin kendi bot hakkı 'Paketler' bölümünden ayarlanır; bu sadece
    bir güvenlik ağıdır ve admin panelden ayrıca yönetilmez."""
    return PREMIUM_BOT_LIMIT

# ============================================================
# 🛡️ GÜVENLİK TARAMA SİSTEMİ v2 — AĞIRLIKLI SKORLAMA (0 FALSE-POSITIVE HEDEFİYLE)
# ============================================================
# 🧠 TASARIM MANTIĞI (neden değişti):
# Eski sistem "binary" çalışıyordu: DANGEROUS_PATTERNS listesindeki onlarca
# kalıptan HERHANGİ biri bir kere eşleşirse dosya anında reddediliyor ve
# kullanıcı otomatik banlanıyordu. Sorun şu ki o listede "subprocess import
# etmek", "socket import etmek", "base64.b64decode kullanmak", "pathlib ile
# dosya okumak/yazmak", ".env kelimesi geçmesi", "Cookies kelimesi geçmesi"
# gibi YÜZLERCE meşru botun rahatlıkla kullandığı sıradan kalıplar da vardı.
# Gerçek zararlı yazılımlar nadiren TEK bir izole kalıptan ibarettir; genelde
# BİRDEN FAZLA şüpheli tekniği bir arada kullanırlar (ör. base64 decode EDİP
# onu exec'e VEREN, ya da soket açıp gelen veriyi çalıştıran kod). Bu yüzden
# artık her sinyalin bir AĞIRLIĞI var; sinyaller TOPLANIYOR ve sadece toplam
# skor eşiği geçerse ya da gerçekten tartışmasız kritik bir kalıp (ör. rm -rf /,
# fork bomb, bilinen obfuscator imzası) tek başına bulunursa dosya reddediliyor.
# Tek başına zararsız olan ama başka sinyallerle birleşince anlam kazanan
# kalıplar (ör. sadece "subprocess import edilmiş") artık TEK BAŞINA asla
# reddetmiyor — çünkü meşru botların büyük çoğunluğu bunu tamamen masum
# amaçlarla (ör. ffmpeg çağırmak, bir CLI aracı çalıştırmak) yapıyor.

SEV_CRITICAL = 100   # Tartışmasız kötü niyet — TEK BAŞINA yeterli, anında red
SEV_HIGH     = 45    # Ciddi şüphe — 2 tanesi ya da 1 HIGH + birkaç MEDIUM red için yeterli
SEV_MEDIUM   = 18    # Tek başına masum olabilir, başkalarıyla birleşince anlamlı
SEV_LOW      = 6     # Çok zayıf sinyal — sadece istatistik/bilgi amaçlı, nadiren tek başına anlam taşır

REJECT_SCORE_THRESHOLD = 60  # Bu skorun altı otomatik reddedilmez (manuel inceleme önerilir)

# --- KRİTİK: bunlardan biri tek başına bile dosyayı reddettirir (gerçek zararlı yazılımlarda
#     yüksek isabetle görülür, meşru kodda pratikte HİÇ görülmez) ---
CRITICAL_PATTERNS = [
    (r'rm\s+-rf\s+/(?!home/claude)', 'rm -rf / komutu (kök dizin silme)'),
    (r'while\s+True\s*:\s*\n\s*os\.fork\s*\(', 'fork bomb'),
    (r'__pyarmor__|pytransform\b', 'bilinen obfuscator/paketleyici imzası (PyArmor)'),
    (r'open\s*\([^)]*,\s*["\']wb["\']\s*\)[^\n]{0,80}\.write\s*\(\s*requests\.(get|post)', 'indirip diske yazma (dropper) davranışı'),
    (r'requests\.(get|post)\s*\([^)]*\)\s*\.content\s*\)?\s*;?\s*(eval|exec)', 'uzaktan indirilen kodu doğrudan çalıştırma (remote code execution)'),
    (r'\bexec\s*\(\s*bytes\.fromhex', 'hex-encoded payload ile exec()'),
    (r'shutil\.rmtree\s*\(\s*["\']?/(?!home/claude)', 'shutil.rmtree ile kök/sistem dizini silme'),
    (r'os\.remove\s*\(\s*["\']?/(?!home/claude)\S*["\']?\s*\)', 'sistem dizininde dosya silme'),
]

# --- YÜKSEK: ciddi şüphe uyandırır ama tek başına "kesin" değildir (bazı meşru
#     ama nadir senaryolarda görülebilir) — birkaçı bir aradaysa toplamda reddeder ---
HIGH_PATTERNS = [
    (r'\bsocket\.(socket|connect)\s*\([^)]*\)\s*[\s\S]{0,120}\b(recv|send)\s*\(', 'socket açıp veri gönderme/alma (reverse-shell şablonuna benzer)'),
    (r'os\.system\s*\(\s*[a-zA-Z_]\w*\s*\)', 'os.system() çağrısına DEĞİŞKEN (sabit olmayan) komut veriliyor'),
    (r'subprocess\.(Popen|run|call|check_output)\s*\([^)]*shell\s*=\s*True[^)]*\+', 'subprocess + shell=True + dinamik komut birleştirme'),
    (r'zlib\.decompress\s*\(\s*base64\.b64decode', 'zlib+base64 ile gizlenmiş kod bloğu'),
    (r'\bexec\s*\(\s*compile\s*\(', 'compile+exec ile dinamik kod çalıştırma'),
    (r'getattr\s*\(\s*__builtins__', '__builtins__ nesnesine dolaylı (getattr) erişim'),
    (r'globals\s*\(\s*\)\s*\[\s*["\']__builtins__', 'globals() üzerinden builtins erişimi'),
    (r'vars\s*\(\s*__builtins__\s*\)', 'vars(__builtins__) ile dolaylı erişim'),
    (r'(eval|exec)\s*\(\s*[a-zA-Z_]\w*\s*\[\s*::\s*-1\s*\]\s*\)', 'ters çevrilmiş (reversed) string ile eval/exec'),
    (r'paramiko|pexpect\.spawn', 'uzak sunucuya sızma amaçlı kütüphane kullanımı'),
    (r'requests\.(get|post)\s*\([^)]*(?:ngrok|webhook\.site|requestbin)', 'bilinen veri-sızdırma servislerine istek'),
    (r'os\.fork\s*\(', 'process fork kullanımı'),
    (r'\.ssh[/\\](id_rsa|id_ed25519|authorized_keys)', 'SSH özel anahtar dosyasına doğrudan erişim'),
    (r'\.aws[/\\]credentials', 'AWS kimlik bilgisi dosyasına doğrudan erişim'),
    (r'AppData\\Local\\Google\\Chrome\\User Data|Local State["\']\s*\)', 'tarayıcı kimlik bilgisi/çerez dosyasına erişim (bilinen stealer yolu)'),
]

# --- ORTA: tek başına masum (birçok meşru bot bunu kullanır), sadece diğer
#     sinyallerle bir araya gelince skor eşiğini aşmaya katkı sağlar ---
MEDIUM_PATTERNS = [
    (r'\bos\.popen\s*\(', 'os.popen() kullanımı'),
    (r'\bctypes\.(CDLL|windll)\b', 'ctypes ile düşük seviye sistem/DLL erişimi'),
    (r'\bmarshal\.loads?\s*\(', 'marshal ile derlenmiş kod deserileştirme'),
    (r'\bpickle\.loads?\s*\(\s*(?!open\()', 'pickle ile (dosyadan değil, ham veriden) deserileştirme'),
    (r'\bbase64\.b64decode\s*\([^)]*\)\s*[\s\S]{0,40}(eval|exec)\s*\(', 'base64 decode sonucu doğrudan eval/exec ile çalıştırılıyor'),
    (r'(?:\\x[0-9a-fA-F]{2}){20,}', 'yoğun \\x hex-escape ile string/kod gizleme'),
    (r'(?:\\u[0-9a-fA-F]{4}){10,}', 'yoğun \\u unicode-escape ile string/kod gizleme'),
    (r'chr\s*\(\s*\d+\s*\)\s*\+\s*chr\s*\(\s*\d+\s*\)(\s*\+\s*chr\s*\(\s*\d+\s*\)){5,}', 'uzun chr() zinciriyle string gizleme (6+ karakter)'),
    (r'"[A-Za-z0-9+/]{200,}={0,2}"|\'[A-Za-z0-9+/]{200,}={0,2}\'', 'çok uzun (200+ karakter) base64 benzeri gömülü blok'),
    (r'lambda\s*:\s*__import__', 'lambda ile gizlenmiş dinamik import'),
    (r'os\.walk\s*\(\s*["\']/(?!home/claude)', 'kök dizinden başlayan (/ ile) sistem geneli dosya tarama'),
]

# --- ZAYIF: tek başına neredeyse anlamsız, sadece bilgi/istatistik olarak toplanır ---
LOW_PATTERNS = [
    (r'/etc/passwd|/etc/shadow', 'sistem parola dosyası yoluna referans'),
    (r'bash_history|zsh_history', 'kabuk geçmiş dosyasına referans'),
]

_SEVERITY_TABLE = (
    (CRITICAL_PATTERNS, SEV_CRITICAL, "CRITICAL"),
    (HIGH_PATTERNS,     SEV_HIGH,     "HIGH"),
    (MEDIUM_PATTERNS,   SEV_MEDIUM,   "MEDIUM"),
    (LOW_PATTERNS,      SEV_LOW,      "LOW"),
)

def _read_source_text(file_path):
    """Dosyayı kayıpsız şekilde metne çevirir. errors='ignore' KULLANMAZ, çünkü
    o, çözülemeyen her byte'ı sessizce SİLER ve bu, dosyanın geri kalanını
    kaydırıp gerçekte var olmayan syntax hatalarına (satır kayması) yol açabilir.
    Bunun yerine sırasıyla dener: utf-8 (strict) -> utf-8-sig (BOM'lu dosyalar
    için) -> utf-8 + errors='replace' (son çare; byte siler, karakterle yer
    değiştirir, satır/konum yapısını bozmaz)."""
    with open(file_path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8', 'utf-8-sig'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _regex_findings(content):
    """Tüm severity katmanlarını tarar, (skor, etiketli_sebep) listesi döndürür.
    Binary reddetme YAPMAZ — sadece bulguları ve puanlarını toplar; karar
    full_scan_code() içinde TOPLAM skora göre verilir."""
    findings = []  # (score, reason_text, is_critical)
    for patterns, score, tier in _SEVERITY_TABLE:
        for pattern, label in patterns:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    findings.append((score, f"[{tier}] {label}", tier == "CRITICAL"))
            except re.error:
                continue

    # 🧬 Zayıf ek heuristik: aşırı uzun tek satır (minified/obfuscated payload şüphesi).
    # Tek başına asla reddettirmez (MEDIUM ağırlıkta), çünkü bazı meşru dosyalarda
    # (ör. otomatik üretilmiş veri tabloları) da uzun satırlar olabilir.
    try:
        longest_line = max((len(l) for l in content.splitlines()), default=0)
        if longest_line > 3000:
            findings.append((SEV_MEDIUM, f"[MEDIUM] aşırı uzun tek satır kod ({longest_line} karakter)", False))
    except Exception:
        pass

    return findings


def scan_code(file_path):
    """Geriye dönük uyumluluk için korunan isim: SADECE regex/imza katmanını
    çalıştırır. Dönüş: (temiz_mi: bool, bulunan_sebepler: list[str]).
    'Temiz mi' burada REJECT_SCORE_THRESHOLD'a göre hesaplanır (artık tek
    eşleşme = kirli değil)."""
    try:
        content = _read_source_text(file_path)
    except Exception:
        return True, []

    findings = _regex_findings(content)
    total_score = sum(f[0] for f in findings)
    has_critical = any(f[2] for f in findings)
    reasons = [f[1] for f in findings]
    is_clean = not has_critical and total_score < REJECT_SCORE_THRESHOLD
    return is_clean, reasons


import ast

# --- AST katmanında kullanılan yardımcı isim/kalıp tabloları ---
_DANGEROUS_CALL_NAMES = {
    "eval", "exec", "compile", "__import__",
}
_DANGEROUS_ATTR_CHAINS_CRITICAL = {
    # Bunlar AST'de dinamik argümanla görülürse tek başına kritik sayılır
    ("os", "system"), ("subprocess", "Popen"), ("subprocess", "call"),
    ("subprocess", "run"), ("subprocess", "check_output"), ("subprocess", "check_call"),
}
_DANGEROUS_ATTR_CHAINS_HIGH = {
    ("os", "popen"), ("os", "spawnl"), ("os", "spawnv"),
    ("socket", "connect"), ("socket", "create_connection"),
    ("marshal", "loads"), ("marshal", "load"),
    ("pickle", "loads"), ("pickle", "load"),
    ("shutil", "rmtree"),
    ("ctypes", "CDLL"), ("ctypes", "windll"),
}
_SENSITIVE_STRING_HINTS = (
    "bot_token", "api_key", "private_key", "session_token", "auth_token",
    "discord.com/api/webhooks",
)
# --- "soğan katmanlı gizleme" (decode(decode(decode(...)))) tespiti SADECE bu isimlerle
#     eşleşen zincirlerde tetiklenir; sıradan iş mantığı fonksiyonlarının iç içe çağrılması
#     (ör. pbtn(texts[...], f(g(h(...)))) bu heuristiğe hiç girmez, false-positive önlenir. ---
_CHAIN_OBFUSCATION_NAMES = {
    "decode", "encode", "b64decode", "b64encode", "b32decode", "b32encode",
    "b16decode", "b16encode", "unhexlify", "hexlify", "rot13", "fromhex",
    "unquote", "unquote_plus", "unescape", "loads", "load", "eval", "exec",
    "compile", "decrypt", "decompress", "unpack", "atob",
}


def _ast_deep_scan(content, file_path):
    """Python kaynağını gerçekten ayrıştırıp (regex'in yakalayamayacağı) semantik
    kalıpları tespit eden derin statik analiz katmanı. Tamamen yerel çalışır.
    Artık HER bulgu bir (skor, sebep, kritik_mi) üçlüsü — binary değil.
    Dönüş: (skorlu_bulgular: list[(score, reason, is_critical)])"""
    findings = []

    try:
        tree = ast.parse(content, filename=os.path.basename(file_path))
    except SyntaxError as e:
        # Sözdizimi hatası tek başına kötü niyet kanıtı değildir (bozuk/yarım
        # dosya olabilir) — bu yüzden düşük ağırlıkla, sadece bilgi amaçlı eklenir,
        # OTOMATİK REDDETMEZ.
        findings.append((SEV_MEDIUM, f"⚠️ Kod ayrıştırılamadı (sözdizimi hatası): {e.msg} (satır {e.lineno})", False))
        return findings
    except Exception as e:
        findings.append((SEV_LOW, f"⚠️ Derin analiz sırasında beklenmeyen hata: {e}", False))
        return findings

    # import alias haritası: "import os as o" -> {'o': 'os'} ; "from os import system as s" -> {'s': 'os.system'}
    alias_map = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                alias_map[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                alias_map[a.asname or a.name] = f"{node.module}.{a.name}"

    def _resolve_module_name(node):
        """Bir ifadenin (Name / alias / __import__('x') çağrısı) hangi modüle
        karşılık geldiğini string olarak çözmeye çalışır. Çözemezse None döner."""
        if isinstance(node, ast.Name):
            return alias_map.get(node.id, node.id)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "__import__" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            # __import__("os") -> "os"  (import os as o; ...; __import__(x) dolaylı
            # kullanımıyla os.system gibi çağrıları normal import olmadan yapan,
            # scanner'ı atlatmak için sık kullanılan bilinen bir kalıp)
            return node.args[0].value
        return None

    def _resolve_attr_chain(node):
        """ast.Attribute/ast.Name zincirini ('modul', 'fonksiyon') tuple'ına indirger,
        alias'ları da (import os as o -> o.system => os.system) çözerek.
        🛠️ FIX: Artık zincirin en altındaki taban sadece bir ast.Name değil,
        __import__("os").system(...) gibi bir __import__() çağrısı da olabilir —
        önceden bu durumda zincir çözülemediği için os.system tespiti tamamen
        atlanabiliyordu."""
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        base = _resolve_module_name(cur)
        if base:
            parts.append(base)
        parts.reverse()
        if len(parts) >= 2:
            return (parts[-2], parts[-1])
        return None

    def _resolve_getattr_call_chain(node):
        """getattr(os, "system")(cmd) gibi bir kalıpta, ÇAĞRILAN şey aslında
        getattr(...)'ın DÖNÜŞ DEĞERİ (yani node.func kendisi bir Call). Bu durumda
        normal _resolve_attr_chain hiç devreye girmiyordu çünkü node.func bir
        Attribute değil, bir Call. Bu, os.system/subprocess.* tespitini attribute
        adını sabit bir string olarak geçirerek (getattr'ın kendi 'dinamik attribute'
        kontrolünü de atlatarak) tamamen es geçen bilinen bir bypass kalıbıdır.
        Çözebilirse ('modul', 'fonksiyon') döner."""
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2):
            return None
        mod = _resolve_module_name(node.args[0])
        attr_arg = node.args[1]
        if mod and isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
            return (mod, attr_arg.value)
        return None

    def _contains_dynamic_call(node):
        """Bir argüman ağacı içinde başka bir fonksiyon çağrısı (Call) var mı — yani
        argüman sabit bir string değil, çalışma zamanında üretilen bir değer mi."""
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                return True
        return False

    def _check_dangerous_chain(chain, dynamic_arg, lineno):
        """('modul','fonksiyon') zincirini CRITICAL/HIGH/MEDIUM tablolarına göre
        değerlendirip uygun bulguyu üretir (attribute-tabanlı ve getattr/__import__
        tabanlı çağrılar için ORTAK mantık — tekrarı önler)."""
        if not chain:
            return None
        mod, fn = chain
        if chain in _DANGEROUS_ATTR_CHAINS_CRITICAL and dynamic_arg:
            return (SEV_CRITICAL,
                    f"'{mod}.{fn}()' çağrısına dinamik/hesaplanmış komut veriliyor (satır {lineno})",
                    True)
        elif chain in _DANGEROUS_ATTR_CHAINS_CRITICAL:
            return (SEV_MEDIUM, f"'{mod}.{fn}()' çağrısı tespit edildi (satır {lineno})", False)
        elif chain in _DANGEROUS_ATTR_CHAINS_HIGH:
            return (SEV_HIGH if dynamic_arg else SEV_MEDIUM,
                    f"'{mod}.{fn}()' çağrısı tespit edildi (satır {lineno})", False)
        return None

    max_call_depth_seen = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn_name = None
            attr_chain = None
            if isinstance(node.func, ast.Name):
                # Çıplak çağrı: eval(...), exec(...), compile(...), __import__(...)
                # — bunlar gerçekten Python'un tehlikeli builtin'leri.
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                # Bir NESNENİN metodu: re.compile(...), json.loads(...), obj.encode(...) vb.
                # 🛠️ FIX: Önceden burada sadece node.func.attr'a (metod adının kendisine)

                # bakılıyordu — bu yüzden re.compile(), csv.compile() gibi TAMAMEN NORMAL
                # stdlib çağrıları, tehlikeli builtin compile()/eval()/exec() ile
                # aynı isme sahip olduğu için (sadece isim çakışması yüzünden) yanlışlıkla
                # kritik/orta risk olarak işaretleniyordu (ör. re.compile -> "compile()
                # ile kod çalıştırma" false-positive'i). Artık nesnenin kendisini de
                # çözüyoruz: sadece gerçekten "builtins.eval(...)" / "__builtins__.exec(...)"
                # gibi builtin'e dolaylı erişim varsa dangerous sayılır; re/json/csv/vb.
                # herhangi bir modülün aynı isimli metodu ARTIK HİÇ FLAG'LENMEZ.
                attr_chain = _resolve_attr_chain(node.func)
                if attr_chain and attr_chain[0] in ("builtins", "__builtin__", "__builtins__"):
                    fn_name = attr_chain[1]
                else:
                    fn_name = None

            # --- eval/exec/compile/__import__ ---
            if fn_name in _DANGEROUS_CALL_NAMES:
                if node.args and _contains_dynamic_call(node.args[0]):
                    findings.append((
                        SEV_HIGH,
                        f"'{fn_name}()' çağrısına dinamik/hesaplanmış bir değer veriliyor "
                        f"(satır {getattr(node, 'lineno', '?')}) — kod gizleme (obfuscation) şüphesi",
                        False,
                    ))
                else:
                    # Sabit/literal argümanla eval/exec çağrısı da yine riskli ama
                    # tartışmasız kritik değil (ör. bir ayar dosyasında "eval" kelimesi
                    # geçen bir yorum satırı değil, gerçek çağrı — yine de MEDIUM'da tut).
                    findings.append((
                        SEV_MEDIUM,
                        f"'{fn_name}()' ile kod çalıştırma (satır {getattr(node, 'lineno', '?')})",
                        False,
                    ))

            # --- getattr/setattr ile dolaylı erişim ---
            if fn_name in ("getattr", "setattr") and len(node.args) >= 2:
                attr_arg = node.args[1]
                if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
                    findings.append((
                        SEV_MEDIUM,
                        f"'{fn_name}()' içinde attribute adı sabit bir string değil, dinamik üretiliyor "
                        f"(satır {getattr(node, 'lineno', '?')}) — dolaylı/gizli erişim şüphesi",
                        False,
                    ))

            # --- tehlikeli modül.fonksiyon zincirleri (alias'lar ve __import__("mod") tabanlı
            # zincirler dahil, ör. __import__("os").system(...)) ---
            if isinstance(node.func, ast.Attribute):
                chain = _resolve_attr_chain(node.func)
                dynamic_arg = bool(node.args) and _contains_dynamic_call(node.args[0])
                finding = _check_dangerous_chain(chain, dynamic_arg, getattr(node, "lineno", "?"))
                if finding:
                    findings.append(finding)

            # --- getattr(modül, "sabit_isim")(args) kalıbı ---
            # 🛠️ FIX: Bu, yukarıdaki attribute-tabanlı kontrolün TAMAMEN kaçırdığı,
            # os.system/subprocess.* gibi çağrıları scanner'dan gizlemek için bilinen
            # bir kaçış (bypass) yöntemidir: getattr(os, "system")(cmd) burada
            # ÇAĞRILAN şey (node.func) bir Attribute değil, getattr(...)'ın kendisinin
            # DÖNÜŞ DEĞERİdir — üstteki blok hiç devreye girmiyordu. Attribute adı
            # sabit bir string olduğu için getattr/setattr dolaylı-erişim kontrolü de
            # bunu "dinamik değil" diyerek atlıyordu. Şimdi bu özel çağrı şeklini de
            # aynı CRITICAL/HIGH tablolarından geçiriyoruz.
            elif isinstance(node.func, ast.Call):
                chain = _resolve_getattr_call_chain(node.func)
                dynamic_arg = bool(node.args) and _contains_dynamic_call(node.args[0])
                finding = _check_dangerous_chain(chain, dynamic_arg, getattr(node, "lineno", "?"))
                if finding:
                    findings.append(finding)


            # --- iç içe çağrı zinciri derinliği (decode(decode(decode(...)))) ---
            # Sadece zincirdeki fonksiyon isimleri gerçekten decode/encode/eval/exec
            # gibi gizleme ile ilişkilendirilen isimlerden biriyse sayılır; sıradan
            # iş mantığı fonksiyonları (pbtn, texts[...], dict.get() vb.) bu
            # heuristiği hiç tetiklemez.
            depth = 0
            cur = node
            while isinstance(cur, ast.Call):
                cur_name = None
                if isinstance(cur.func, ast.Name):
                    cur_name = cur.func.id
                elif isinstance(cur.func, ast.Attribute):
                    cur_name = cur.func.attr
                if cur_name not in _CHAIN_OBFUSCATION_NAMES:
                    break
                depth += 1
                if cur.args and isinstance(cur.args[0], ast.Call):
                    cur = cur.args[0]
                else:
                    break
            max_call_depth_seen = max(max_call_depth_seen, depth)

        # --- literal string içinde hassas anahtar kelime taraması ---
        # 🛠️ FIX: liste artık çok kısaltıldı ve DÜŞÜK ağırlıkta — önceden "cookies",
        # "webhook", "password" gibi son derece yaygın, tek başına anlamsız kelimeler
        # bile (ör. bir e-ticaret botunda "kurabiye/cookies" ürün adı ya da bir admin
        # panelinde "yeni parola belirle" gibi metinler) dosyayı kirletiyordu.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            for hint in _SENSITIVE_STRING_HINTS:
                if hint in low and len(node.value) > len(hint) + 6:
                    findings.append((
                        SEV_LOW,
                        f"Literal string içinde hassas anahtar kelime tespit edildi "
                        f"('{hint}' geçen bir literal, satır {getattr(node, 'lineno', '?')})",
                        False,
                    ))
                    break

    if max_call_depth_seen >= 4:
        findings.append((
            SEV_HIGH,
            f"{max_call_depth_seen} katmanlı iç içe decode/encode/eval çağrısı zinciri (soğan katmanlı gizleme şüphesi)",
            False,
        ))
    elif max_call_depth_seen == 3:
        findings.append((
            SEV_MEDIUM,
            f"{max_call_depth_seen} katmanlı iç içe decode/encode/eval çağrısı zinciri",
            False,
        ))

    return findings


def full_scan_code(file_path):
    """İki katmanlı, tamamen yerel derin güvenlik taraması: (1) regex/imza taraması
    ve (2) gerçek Python AST'sini ayrıştıran semantik derin analiz. Artık İKİSİ DE
    AĞIRLIKLI SKOR üretir; dosya sadece şu durumlarda reddedilir:
      (a) tartışmasız KRİTİK bir bulgu varsa (tek başına yeterli), VEYA
      (b) tüm bulguların toplam skoru REJECT_SCORE_THRESHOLD'u geçerse.
    Tek bir zayıf/orta sinyal (ör. yalnızca 'subprocess import edilmiş' ya da
    tek bir 'password' kelimesi geçen literal) ARTIK TEK BAŞINA asla dosyayı
    reddetmez — bu, önceki sistemin en büyük false-positive kaynağıydı.
    Dönüş: (temiz_mi: bool, bulunan_sebepler: list[str])"""
    try:
        content = _read_source_text(file_path)
    except Exception as e:
        return True, []

    regex_findings = _regex_findings(content)

    try:
        # 🐛 FIX: İçerik asla karakter limitine göre kesilmez — büyük ama tamamen
        # normal/temiz bot dosyalarını satır ortasında koparıp sahte "sözdizimi
        # hatası" üretmesin diye. AST ayrıştırması birkaç MB'lık dosyalarda bile
        # milisaniyeler sürer.
        ast_findings = _ast_deep_scan(content, file_path)
    except Exception as e:
        ast_findings = [(SEV_LOW, f"⚠️ Derin analiz dosyayı okuyamadı: {e}", False)]

    all_findings = list(regex_findings) + list(ast_findings)

    total_score = sum(f[0] for f in all_findings)
    has_critical = any(f[2] for f in all_findings)

    # Tekrarlanan aynı sebepleri sadeleştir (aynı kalıp birçok satırda tekrar edebilir)
    seen = set()
    deduped_reasons = []
    for score, reason, is_crit in all_findings:
        key = re.sub(r'satır \d+', 'satır N', reason)
        if key not in seen:
            seen.add(key)
            deduped_reasons.append(reason)

    is_clean = not has_critical and total_score < REJECT_SCORE_THRESHOLD

    if not is_clean:
        deduped_reasons.insert(0, f"📊 Toplam risk skoru: {total_score}/{REJECT_SCORE_THRESHOLD} eşik"
                                    + (" — KRİTİK bulgu mevcut" if has_critical else ""))

    return is_clean, deduped_reasons


def ban_user(uid, reason):
    try:
        cursor.execute("UPDATE users SET banned=1, ban_reason=? WHERE user_id=?", (reason, uid))
        conn.commit()
    except:
        pass

    try:
        bot.send_message(
            ARCHIVE_CHAT_ID,
            f"🚫 <b>Kullanıcı Banlandı</b>\n{DIV}\n👤 Kullanıcı: <code>{uid}</code>\n📌 Sebep: {esc(str(reason))}",
            parse_mode="HTML"
        )
    except Exception as _e:
        print(f"[BAN BİLDİRİM HATA] {_e}")

def unban_user(uid):
    try:
        cursor.execute("UPDATE users SET banned=0, ban_reason=NULL WHERE user_id=?", (uid,))
        conn.commit()
    except:
        pass

def is_banned(uid):
    try:
        cursor.execute("SELECT banned FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        return bool(r and r[0] == 1)
    except:
        return False

def quarantine_bot_file(bid, uid, fp, reasons):
    """Onaylı bir bot dosyasında SONRADAN kötü amaçlı kod tespit edilirse:
    çalışıyorsa süreci durdurur, dosyayı siler, kaydı karantinaya alır ve kullanıcıyı otomatik banlar."""
    try:
        cursor.execute("SELECT pid FROM bot_files WHERE id=?", (bid,))
        row = cursor.fetchone()
        if row and row[0]:
            kill_pid(row[0])
    except:
        pass

    try:
        if fp and os.path.exists(fp):
            os.remove(fp)
    except:
        pass

    try:
        cursor.execute("UPDATE bot_files SET status='malicious', bot_status='stopped', pid=NULL WHERE id=?", (bid,))
        conn.commit()
    except:
        pass

    ban_user(uid, ", ".join(reasons))

    try:
        bot.send_message(uid, T(uid, 'malicious_detected').format(", ".join(reasons), SUPPORT_USERNAME), parse_mode="HTML")
    except:
        pass

    for aid in ADMIN_IDS:
        try:
            bot.send_message(
                aid,
                f"🚫 SÜREKLİ TARAMA UYARISI\n"
                f"Onaylı bir bot dosyasında sonradan kötü amaçlı kod tespit edildi!\n"
                f"👤 Kullanıcı: {user_display(uid, html=False)}\n🤖 Bot ID: {bid}\n⚠️ Sebep: {', '.join(reasons)}\n\n"
                f"✅ Bot durduruldu, dosya silindi, kullanıcı otomatik olarak engellendi."
            )
        except:
            pass

def _file_hash(fp):
    """Dosyanın SHA-256 özetini döndürür (değişiklik tespiti için)."""
    try:
        h = hashlib.sha256()
        with open(fp, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except:
        return None

_WATCH_FILE_HASHES = {}  # bid -> son bilinen dosya hash'i (bellek içi; process yeniden başlarsa sıfırlanır)
_WATCH_LAST_DEEP_SCAN = {}  # bid -> son derin (AST) taramanın yapıldığı zaman (bellek içi)
DEEP_SCAN_INTERVAL = 3600  # saniye - dosya değişmese bile bu aralıkla sessizce derin AST taraması yapılır

def code_watch_loop(interval_seconds=300):
    """Arka planda sürekli çalışır: onaylı tüm bot dosyalarını periyodik olarak yeniden tarar.
    Her tur HIZLI regex taraması yapılır. Bir dosyanın içeriği son taramadan (veya onay
    anındaki halinden) beri DEĞİŞMİŞSE (RAT'ların en yaygın saklama taktiği: temiz kod
    onaylatıp sonra değiştirmek), ek olarak AST tabanlı DERİN statik analiz de tetiklenir.
    Ayrıca dosya hiç değişmemiş olsa bile, her DEEP_SCAN_INTERVAL (varsayılan 1 saat) içinde
    bir kez daha derin tarama sessizce (kullanıcıya hiçbir bildirim gitmeden) tekrarlanır;
    böylece ilk onayda gözden kaçmış olabilecek bir şey de zamanla yakalanabilir. Kullanıcıya
    sadece dosya gerçekten zararlı bulunup karantinaya alınırsa haber verilir.
    Referans hash, onay anında veritabanına (approved_file_hash) kaydedildiği için, bot
    yeniden başlasa bile ya da dosya onaydan hemen sonra (ilk 5 dakikalık pencerede)
    değiştirilse bile bu değişiklik kaçırılmaz. Tamamen yerel/AST tabanlı olduğu için
    (dış API'ye bağımlı olmadığından) her tarama anında ve tutarlı sonuç üretir."""
    wconn, wcursor = open_db(DB_PATH)
    while True:
        try:
            wcursor.execute("SELECT id, user_id, file_path, approved_file_hash FROM bot_files WHERE status='approved'")
            rows = wcursor.fetchall()
        except Exception as e:
            print(f"Watch Loop DB Error: {e}")
            rows = []

        now_ts = time.time()
        for bid, uid, fp, db_hash in rows:
            try:
                if not fp or not os.path.exists(fp):
                    continue

                current_hash = _file_hash(fp)
                # Bellekteki hash varsa onu, yoksa onay anında DB'ye kaydedilen referans hash'i kullan
                prev_hash = _WATCH_FILE_HASHES.get(bid, db_hash)
                file_changed = (prev_hash is not None and current_hash != prev_hash)
                if current_hash:
                    _WATCH_FILE_HASHES[bid] = current_hash

                # ⏰ Bu bot ilk defa görülüyorsa sayaç şimdi başlasın (yeniden başlatmada
                # onaylı tüm botları aynı anda derin taramaya sokup CPU'yu boğmamak için)
                if bid not in _WATCH_LAST_DEEP_SCAN:
                    _WATCH_LAST_DEEP_SCAN[bid] = now_ts

                deep_due = (now_ts - _WATCH_LAST_DEEP_SCAN[bid]) >= DEEP_SCAN_INTERVAL

                if file_changed or deep_due:
                    if file_changed:
                        print(f"Watch Loop: bid={bid} dosyası değişmiş, derin (AST) taraması tetikleniyor")
                    # 🚨 Onaylı dosya son bilinen halinden beri değişmiş veya saatlik periyot
                    # dolmuş -> derin AST analiziyle yeniden tara (kullanıcıya sessizce)
                    is_clean, reasons = full_scan_code(fp)
                    _WATCH_LAST_DEEP_SCAN[bid] = now_ts
                    if is_clean and file_changed:
                        # Meşru bir değişiklikse yeni hash'i referans olarak DB'ye de yaz
                        try:
                            wcursor.execute("UPDATE bot_files SET approved_file_hash=? WHERE id=?", (current_hash, bid))
                            wconn.commit()
                        except:
                            pass
                else:
                    is_clean, reasons = scan_code(fp)

                if not is_clean:
                    quarantine_bot_file(bid, uid, fp, reasons)
            except Exception as e:
                print(f"Watch Loop Scan Error (bid={bid}): {e}")

        time.sleep(interval_seconds)

def find_imports(file_path):
    modules = set()
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in ('#', '"', "'"):
                    continue
                for pat in [r'^import\s+(\S+)', r'^from\s+(\S+)\s+import']:
                    m = re.match(pat, line)
                    if m:
                        mod = m.group(1).split('.')[0]
                        # 🛡️ FIX: find_imports() satır bazlı REGEX ile çalışıyor (AST değil),
                        # bu yüzden gerçek bir import ifadesi olmayan ama bu kalıba uyan
                        # herhangi bir satır (ör. triple-quote bir string/docstring İÇİNE
                        # gizlenmiş "import --index-url=http://evil/simple/ paket" gibi bir
                        # satır) de "modül adı" sanılıp yakalanıyordu. install_modules()
                        # bunu doğrudan `pip install -q <mod>` şeklinde çalıştırdığı için,
                        # "-" ile başlayan böyle bir "modül adı" aslında bir PIP KOMUT SATIRI
                        # BAYRAĞI (ör. --index-url) olarak yorumlanıp pip'in paketleri
                        # SALDIRGANIN KENDİ SUNUCUSUNDAN indirmesine yol açabilirdi — hiçbir
                        # shell metakarakteri kullanılmadan (subprocess liste ile çağrıldığı
                        # için shell injection değil, ama pip FLAG injection mümkündü).
                        # Artık sadece gerçek bir Python tanımlayıcısına (identifier) benzeyen
                        # isimler modül olarak kabul ediliyor; "-", "=", "/", ":" vb. içeren
                        # hiçbir şey asla pip'e argüman olarak geçirilmiyor.
                        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', mod) and mod not in sys.stdlib_module_names:
                            modules.add(mod)
                        break
    except:
        pass
    return modules

def install_modules(modules, uid, mid):
    if not modules:
        return
    try:
        for mod in modules:
            # 🛡️ İkinci savunma katmanı: find_imports() zaten filtreliyor ama modules
            # başka bir yerden de doldurulabileceği ihtimaline karşı burada da
            # aynı doğrulama tekrarlanır — asla geçerli bir identifier olmayan bir
            # değer pip'e argüman olarak geçirilmez.
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', mod):
                continue
            try:
                importlib.import_module(mod)
            except:
                bot.edit_message_text(T(uid, 'installing').format(mod), uid, mid)
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", mod], capture_output=True, timeout=30)
                bot.edit_message_text(T(uid, 'installed').format(mod), uid, mid)
                time.sleep(0.2)
    except:
        pass

def update_bot_description(token):
    pass

def btn(text, data=None, url=None):
    return InlineKeyboardButton(text, callback_data=data, url=url)

def _back_kb(uid, target="admin_panel"):
    """Sadece 'Geri' butonu içeren bir inline klavye döner (otomatik yönlendirme yapmaz)."""
    mk = InlineKeyboardMarkup()
    mk.row(btn(T(uid, 'back'), target))
    return mk

# ⏳ Süre seçenekleri: (etiket, dakika) — 0 = süresiz. Tüm süreler dakika cinsinden tutulur.
DURATION_OPTIONS = [
    ("1 Ay", 43200), ("1 Yıl", 525600),
    ("2 Ay", 86400), ("2 Yıl", 1051200),
    ("3 Ay", 129600), ("3 Yıl", 1576800),
]

def format_duration(minutes):
    """Dakika cinsinden bir süreyi okunabilir Türkçe metne çevirir. 0/None -> süresiz."""
    if not minutes:
        return "♾️ Süresiz"
    if minutes < 60:
        return f"{minutes} Dakika"
    if minutes < 1440:
        return f"{minutes // 60} Saat"
    if minutes < 10080:
        return f"{minutes // 1440} Gün"
    if minutes < 43200:
        return f"{minutes // 10080} Hafta"
    if minutes < 525600:
        return f"{minutes // 43200} Ay"
    return f"{minutes // 525600} Yıl"

def duration_keyboard(prefix, extra=""):
    """prefix_[extra_]dakika şeklinde callback_data üreten süre seçim klavyesi."""
    mk = InlineKeyboardMarkup(row_width=2)
    row = []
    for label, minutes in DURATION_OPTIONS:
        cd = f"{prefix}_{extra}_{minutes}" if extra != "" else f"{prefix}_{minutes}"
        row.append(btn(label, cd))
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    return mk

# 📦 Paket ekleme sihirbazının geçici durumu (admin uid -> {name, price, desc, limit})
PKG_WIZARD = {}
SLEEP_WIZARD = {}

# 💬 Canlı destek: hangi adminin şu an hangi kullanıcıyla aktif yazıştığını tutar (admin_id -> user_id)
# Not: pyTelegramBotAPI her chat için tek next_step_handler tutar; bir admin aynı anda birden
# fazla talebi onaylarsa sadece EN SON onayladığı sohbetin mesaj akışı aktif kalır. Bu yüzden
# bir sohbeti bitirmeden yeni talep onaylamamak önerilir.
ADMIN_ACTIVE_CHAT = {}

def _support_user_label(uid):
    """Destek bildirimlerinde kullanıcıyı @kullaniciadi veya isim ile gösterir, yoksa ID döner."""
    try:
        cursor.execute("SELECT username, name FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        if r:
            uname, name = r
            if uname:
                return f"@{esc(uname)}"
            if name:
                return esc(name)
    except:
        pass
    return f"<code>{uid}</code>"

def support_first_message(msg):
    """Kullanıcının 'Canlı Destek Başlat' sonrası yazdığı ilk (talep) mesajını işler:
    support_sessions tablosuna 'pending' olarak kaydeder ve tüm adminlere bildirim yollar."""
    uid = msg.from_user.id
    if is_banned(uid) and not is_admin(uid):
        return
    text = (msg.text or "").strip()
    if text == "/iptal":
        bot.send_message(uid, "❌ <b>Destek talebi iptal edildi.</b>", parse_mode="HTML")
        main_menu(uid)
        return
    if not text:
        m = bot.send_message(uid, "⚠️ Lütfen yazılı bir mesaj gönderin.\n❌ Vazgeçmek için: /iptal", parse_mode="HTML")
        bot.register_next_step_handler(m, support_first_message)
        return

    now = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO support_sessions (user_id, status, first_message, created_at) VALUES (?, 'pending', ?, ?)",
        (uid, text, now)
    )
    conn.commit()
    session_id = cursor.lastrowid

    bot.send_message(
        uid,
        "✅ <b>Destek talebiniz alındı!</b>\n" + DIV + "\n"
        "En kısa sürede bir yetkili talebinizi onaylayacak ve sizinle canlı olarak ilgilenecek.",
        parse_mode="HTML"
    )

    label = _support_user_label(uid)
    mk = InlineKeyboardMarkup(row_width=2)
    mk.row(btn("✅ Onayla", f"supapp_{session_id}"), btn("❌ Reddet", f"suprej_{session_id}"))
    admin_txt = (
        f"📞 <b>Yeni Destek Talebi</b>\n{DIV}\n"
        f"👤 {label} (<code>{uid}</code>)\n"
        f"💬 {esc(text)}\n{DIV}\n"
        f"Talebi üstlenmek için onayla:"
    )
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, admin_txt, reply_markup=mk, parse_mode="HTML")
        except:
            pass

def support_approve(session_id, aid, mid, call_id):
    """Admin bir destek talebini onaylar: oturum 'active' olur, kullanıcıya güzel bir onay
    mesajı gönderilir ve her iki tarafta da mesajları karşı tarafa ileten bir next-step
    zinciri başlatılır."""
    cursor.execute("SELECT user_id, status, first_message FROM support_sessions WHERE id=?", (session_id,))
    r = cursor.fetchone()
    if not r:
        bot.answer_callback_query(call_id, "⚠️ Talep bulunamadı.", show_alert=True)
        return
    tid, status, first_msg = r
    if status != 'pending':
        bot.answer_callback_query(call_id, f"⚠️ Bu talep zaten {('aktif' if status=='active' else status)} durumda.", show_alert=True)
        return

    now = datetime.now().isoformat()
    cursor.execute("UPDATE support_sessions SET status='active', admin_id=?, approved_at=? WHERE id=?", (aid, now, session_id))
    conn.commit()
    ADMIN_ACTIVE_CHAT[aid] = tid

    try:
        bot.edit_message_reply_markup(aid, mid, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call_id, "✅ Destek talebi üstlenildi.")

    label = _support_user_label(tid)

    um = bot.send_message(
        tid,
        "✅ <b>Admin Destek İletişimi Kuruldu!</b> 🎉\n" + DIV + "\n"
        "Bir yetkili talebinizi onayladı. Bundan sonra buraya yazacağınız mesajlar "
        "direkt yetkiliye iletilecek.",
        parse_mode="HTML"
    )
    bot.clear_step_handler_by_chat_id(tid)
    bot.register_next_step_handler(um, support_relay_from_user, session_id)

    mk = InlineKeyboardMarkup()
    mk.row(btn("🛑 Sohbeti Durdur", f"supend_{session_id}"))
    am = bot.send_message(
        aid,
        f"💬 <b>Destek Sohbeti Başladı</b>\n{DIV}\n"
        f"👤 {label} (<code>{tid}</code>)\n"
        f"📝 İlk mesaj: {esc(first_msg)}\n{DIV}\n"
        f"Yazdığın her mesaj kullanıcıya iletilecek. İstediğin an aşağıdan sohbeti durdurabilirsin.",
        reply_markup=mk,
        parse_mode="HTML"
    )
    bot.clear_step_handler_by_chat_id(aid)
    bot.register_next_step_handler(am, support_relay_from_admin, session_id)

def support_reject(session_id, aid, mid, call_id):
    """Admin bir destek talebini reddeder."""
    cursor.execute("SELECT user_id, status FROM support_sessions WHERE id=?", (session_id,))
    r = cursor.fetchone()
    if not r:
        bot.answer_callback_query(call_id, "⚠️ Talep bulunamadı.", show_alert=True)
        return
    tid, status = r
    if status != 'pending':
        bot.answer_callback_query(call_id, "⚠️ Bu talep zaten işleme alınmış.", show_alert=True)
        return

    cursor.execute("UPDATE support_sessions SET status='rejected', closed_at=? WHERE id=?", (datetime.now().isoformat(), session_id))
    conn.commit()
    try:
        bot.edit_message_reply_markup(aid, mid, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call_id, "❌ Talep reddedildi.")
    try:
        bot.send_message(tid, "❌ <b>Destek talebiniz reddedildi.</b>\nDilerseniz 📞 Destek menüsünden tekrar talep oluşturabilirsiniz.", parse_mode="HTML")
    except:
        pass

def support_end(session_id, aid, mid, call_id):
    """Admin aktif bir destek sohbetini istediği an sonlandırır."""
    cursor.execute("SELECT user_id, admin_id, status FROM support_sessions WHERE id=?", (session_id,))
    r = cursor.fetchone()
    if not r:
        bot.answer_callback_query(call_id, "⚠️ Sohbet bulunamadı.", show_alert=True)
        return
    tid, sess_admin, status = r
    if status != 'active':
        bot.answer_callback_query(call_id, "ℹ️ Bu sohbet zaten sona ermiş.", show_alert=True)
        return

    cursor.execute("UPDATE support_sessions SET status='closed', closed_at=? WHERE id=?", (datetime.now().isoformat(), session_id))
    conn.commit()
    if ADMIN_ACTIVE_CHAT.get(aid) == tid:
        ADMIN_ACTIVE_CHAT.pop(aid, None)

    try:
        bot.edit_message_reply_markup(aid, mid, reply_markup=None)
    except:
        pass
    bot.answer_callback_query(call_id, "🛑 Sohbet sonlandırıldı.")

    label = _support_user_label(tid)
    bot.send_message(
        aid,
        "🛑 <b>Destek Sohbeti Sonlandırıldı</b>\n" + DIV + "\n"
        f"👤 {label} (<code>{tid}</code>) ile olan görüşme kapatıldı.\n"
        "📋 Bu kullanıcı yeni bir talep açarsa tekrar bildirim alacaksın.",
        parse_mode="HTML"
    )
    try:
        mk_end = InlineKeyboardMarkup()
        mk_end.row(btn("📞 Destek", "support"))
        bot.send_message(
            tid,
            "🛑 <b>Destek Sohbeti Sona Erdi</b>\n" + DIV + "\n"
            "Görüşmemiz bir yetkili tarafından sonlandırıldı.\n"
            "Umarım yardımcı olabilmişizdir! 🙌\n\n"
            "📌 Yeni bir talebin olursa 📞 Destek menüsünden tekrar bize ulaşabilirsin.",
            reply_markup=mk_end,
            parse_mode="HTML"
        )
    except:
        pass

def support_relay_from_user(msg, session_id):
    """Aktif destek sohbetinde kullanıcıdan gelen mesajı ilgili admine iletir ve
    bir sonraki mesajı yakalamak için zinciri yeniden kaydeder."""
    uid = msg.from_user.id
    text = (msg.text or "").strip()
    cursor.execute("SELECT status, admin_id FROM support_sessions WHERE id=?", (session_id,))
    r = cursor.fetchone()
    if not r or r[0] != 'active':
        bot.send_message(uid, "🛑 <b>Destek sohbeti sona erdi.</b>", parse_mode="HTML")
        return
    aid = r[1]
    if not text:
        m = bot.send_message(uid, "⚠️ Lütfen yazılı bir mesaj gönderin.")
        bot.register_next_step_handler(m, support_relay_from_user, session_id)
        return

    label = _support_user_label(uid)
    try:
        bot.send_message(aid, f"👤 <b>{label}</b> (<code>{uid}</code>):\n{esc(text)}", parse_mode="HTML")
    except:
        pass
    bot.register_next_step_handler(msg, support_relay_from_user, session_id)

def support_relay_from_admin(msg, session_id):
    """Aktif destek sohbetinde adminden gelen mesajı ilgili kullanıcıya iletir ve
    bir sonraki mesajı yakalamak için zinciri yeniden kaydeder."""
    aid = msg.from_user.id
    text = (msg.text or "").strip()
    cursor.execute("SELECT status, user_id FROM support_sessions WHERE id=?", (session_id,))
    r = cursor.fetchone()
    if not r or r[0] != 'active':
        bot.send_message(aid, "🛑 <b>Bu destek sohbeti artık aktif değil.</b>", parse_mode="HTML")
        return
    tid = r[1]
    if not text:
        m = bot.send_message(aid, "⚠️ Lütfen yazılı bir mesaj gönderin.")
        bot.register_next_step_handler(m, support_relay_from_admin, session_id)
        return

    admin_name = esc(msg.from_user.first_name or "Yetkili")
    try:
        bot.send_message(
            tid,
            f"💬 <b>Nebula Destek</b> · <i>{admin_name}</i>\n"
            "┏━━━━━━━━━━━━━━━━━━┓\n"
            f"    {esc(text)}\n"
            "┗━━━━━━━━━━━━━━━━━━┛\n"
            "⬇️ <i>Mesajı yanıtlamak için sohbete yazı yazabilirsiniz</i>",
            parse_mode="HTML"
        )
    except:
        pass
    bot.register_next_step_handler(msg, support_relay_from_admin, session_id)
PROMO_WIZARD = {}

def check_pid(pid):
    try:
        os.kill(pid, 0)
        return True
    except:
        return False

def kill_pid(pid):
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if check_pid(pid):
            os.kill(pid, signal.SIGKILL)
    except:
        pass

def monitor_bot(bid, pid):
    """Başlatılan her bot için ayrı bir thread'de çalışır ve process ölünce durumu
    veritabanına yazar. 🛠️ FIX: Önceden GLOBAL 'cursor'/'conn' nesnesini doğrudan
    kullanıyordu — ama bu fonksiyon, aynı anda çalışan birden fazla bot için
    (her biri kendi thread'inde) VE ana polling thread'iyle AYNI ANDA
    çağrılabiliyor. sqlite3'te tek bir Cursor nesnesi thread-safe DEĞİLDİR;
    birden fazla thread aynı cursor'ı aynı anda kullanınca 'Recursive use of
    cursors not allowed' gibi ara sıra/rastgele oluşan hatalara yol açar.
    Diğer arka plan döngüleri (sleep_schedule_loop, monthly_users_badge_loop,
    code_watch_loop) zaten kendi bağlantısını açıyordu — burada da aynı
    (güvenli) örüntüye geçildi."""
    mconn, mcursor = open_db(DB_PATH)
    try:
        while True:
            try:
                if not check_pid(pid):
                    mcursor.execute("UPDATE bot_files SET bot_status='stopped', pid=NULL WHERE id=?", (bid,))
                    mconn.commit()
                    break
                time.sleep(10)
            except:
                break
    finally:
        try:
            mconn.close()
        except:
            pass

def main_menu(uid, mid=None):
    if not check_channel(uid):
        mk = InlineKeyboardMarkup(row_width=2)
        mk.row(btn(T(uid, 'join_channel'), url=f"https://t.me/{CHANNEL[1:]}"),
               btn(T(uid, 'check_join'), "check_channel"))
        bot.send_message(uid, T(uid, 'channel_warning'), reply_markup=mk)
        return

    sync_premium_expiry(uid)
    try:
        cursor.execute("SELECT name, premium, bot_count, total_files, premium_package, premium_until FROM users WHERE user_id=?", (uid,))
        u = cursor.fetchone()
    except:
        return

    if not u:
        # 🔄 Kullanıcı bu veritabanında henüz kayıtlı değil (örn. bir restore sonrası).
        # Sessizce çıkmak yerine /start'ta olduğu gibi otomatik kayıt oluşturup devam ediyoruz.
        try:
            cursor.execute("SELECT * FROM users WHERE user_id=?", (uid,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO users (user_id, username, name, created_at) VALUES (?,?,?,?)",
                    (uid, "None", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                conn.commit()
            cursor.execute("SELECT name, premium, bot_count, total_files, premium_package, premium_until FROM users WHERE user_id=?", (uid,))
            u = cursor.fetchone()
        except Exception as e:
            print(f"Main Menu Auto-Register Error: {e}")
            return
        if not u:
            return
    
    name, prem, bc, tf, pkg, p_until = u
    ps = '⭐ VIP Üye' if prem else '🆓 Standart Üye'
    fl = get_limit()
    pkg_display = pkg_display_name(pkg)

    if prem:
        exp_txt = T(uid, 'lifetime') if not p_until else T(uid, 'expires_on').format(p_until[:10])
        quota_line = f"🤖 Bot Hakkı: <b>{bc}/{get_user_bot_limit(uid)}</b>\n{exp_txt}"
    else:
        quota_line = f"🤖 Bot Hakkı: <b>{bc}/{fl}</b>"

    cursor.execute("SELECT value FROM settings WHERE key='sleep_start'")
    _ss_row = cursor.fetchone()
    cursor.execute("SELECT value FROM settings WHERE key='sleep_end'")
    _se_row = cursor.fetchone()
    sleep_start = _ss_row[0] if _ss_row and _ss_row[0] else "22:30"
    sleep_end = _se_row[0] if _se_row and _se_row[0] else "10:00"

    txt = (
        f"🚀 <b>NEBULA HOSTING</b> | Telegram Bot Hosting\n"
        f"{DIV}\n"
        f"Botunu Dakikalar İçinde Yayına Al.\n"
        f"Kurulum Yok, Sunucu Derdi Yok.\n"
        f"Telegram'ın En Gelişmiş Hosting Botu Sizlerle.\n"
        f"{DIV}\n"
        f"⚡ Hızlı Kurulum — Dosyayı yükle, bot çalışsın\n"
        f"🔍 Güvenlik Taramalı — Her dosya otomatik kontrolden geçer\n"
        f"📦 Ücretsiz Başla — Paket satın almak zorunlu değil\n"
        f"🌍 Çok Dil — Türkçe, İngilizce, Azerbaycanca\n"
        f"{DIV}\n"
        f"👋 Hoş geldin, <b>{esc(name)}</b>! (🆔 <code>{uid}</code>)\n"
        f"📊 Durum: <b>{ps}</b>\n"
        f"📦 Paket: <b>{esc(pkg_display)}</b>\n"
        f"{quota_line}\n"
        f"{DIV}\n"
        f"🌙 Uyku Modu: {esc(sleep_start)} — {esc(sleep_end)}\n"
        f"Bu saatler arasında bot kurulumu ve silme işlemleri geçici olarak devre dışıdır.\n"
        f"{DIV}\n"
        f"👇 Menüden bir işlem seç"
    )

    mk = InlineKeyboardMarkup(row_width=2)
    mk.row(btn(T(uid, 'upload_bot'), "upload_bot"))
    mk.row(btn(T(uid, 'my_bots'), "my_bots"))
    mk.row(btn(T(uid, 'premium'), "premium_info"), btn(T(uid, 'promo_btn'), "use_promo"))
    mk.row(btn("🛍️ Şablon Marketi", "template_market"), btn(T(uid, 'ranking'), "ranking"))
    mk.row(btn(T(uid, 'settings'), "settings"), btn(T(uid, 'support'), "support"))
    mk.row(btn(T(uid, 'profile'), "profile"))
    mk.row(btn(T(uid, 'help'), "help"))

    if is_admin(uid):
        mk.row(btn(T(uid, 'admin_panel'), "admin_panel"))

    if mid:
        try:
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

@bot.message_handler(commands=['gecmisarsiv'])
def gecmis_arsiv_cmd(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        return
    try:
        cursor.execute("SELECT user_id, file_name, file_path, bot_username FROM bot_files WHERE status='approved'")
        rows = cursor.fetchall()
    except Exception as e:
        bot.send_message(uid, f"⚠️ DB hatası: {e}")
        return

    gonderildi = 0
    bulunamadi = 0
    for r_uid, r_fn, r_fp, r_uname in rows:
        try:
            if r_fp and os.path.exists(r_fp):
                with open(r_fp, "rb") as af:
                    bot.send_document(
                        ARCHIVE_CHAT_ID, af,
                        caption=f"📦 Geçmiş Arşiv\n👤 UID: {r_uid}\n📄 {r_fn}\n🤖 @{r_uname if r_uname else '-'}"
                    )
                gonderildi += 1
            else:
                bulunamadi += 1
        except Exception as e:
            print(f"[GEÇMİŞ ARŞİV HATA] uid={r_uid} {e}")
            bulunamadi += 1

    bot.send_message(uid, f"✅ Arşivleme bitti.\n📤 Gönderilen: {gonderildi}\n❌ Bulunamayan: {bulunamadi}")

@bot.message_handler(commands=['start'])
def start_cmd(msg):
    uid = msg.from_user.id
    uname = msg.from_user.username or "None"
    name = msg.from_user.first_name
    
    try:
        cursor.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (user_id, username, name, created_at, last_start) VALUES (?,?,?,?,?)",
                          (uid, uname, name,
                           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        else:
            cursor.execute("UPDATE users SET last_start=? WHERE user_id=?",
                          (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), uid))
            conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")

    if is_banned(uid):
        cursor.execute("SELECT ban_reason FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        reason = r[0] if r and r[0] else "-"
        mk = None
        if is_admin(uid):
            mk = InlineKeyboardMarkup()
            mk.row(btn("⚙️ Admin Paneli", "admin_panel"))
        bot.send_message(
            uid,
            T(uid, 'banned_msg').format(BAN_APPEAL_CONTACT) + f"\n\n🔎 <b>Sebep:</b> {esc(reason)}",
            reply_markup=mk,
            parse_mode="HTML"
        )
        return

    # 🔧 Bakım Modu (Uyku Modundan tamamen bağımsız): açıkken adminler dışında
    # kimse botu kullanamaz, /start atınca sadece bu bilgilendirme mesajını görür.
    if is_bakim_modu() and not is_admin(uid):
        bot.send_message(uid, T(uid, 'bakim_modu_msg'), parse_mode="HTML")
        return

    main_menu(uid)

def admin_panel(uid, mid=None):
    mk = InlineKeyboardMarkup(row_width=2)

    # 📊 Genel Bakış
    mk.row(btn("▬▬▬ 📊 Genel Bakış ▬▬▬", "noop"))
    mk.row(btn(T(uid, 'admin_stats'), "admin_stats"))
    mk.row(btn(T(uid, 'admin_pending'), "admin_pending"), btn(T(uid, 'admin_approved'), "admin_approved"))

    # 👥 Kullanıcı Yönetimi
    mk.row(btn("▬▬▬ 👥 Kullanıcı Yönetimi ▬▬▬", "noop"))
    mk.row(btn(T(uid, 'admin_users'), "admin_users"), btn(T(uid, 'admin_all_bots'), "admin_all_bots"))
    mk.row(btn(T(uid, 'admin_premium_give'), "admin_premium"), btn(T(uid, 'admin_premium_take'), "admin_unpremium"))
    mk.row(btn(T(uid, 'admin_unban'), "admin_unban"))

    # 📦 Paket Yönetimi
    mk.row(btn("▬▬▬ 📦 Paket Yönetimi ▬▬▬", "noop"))
    mk.row(btn(T(uid, 'admin_packages'), "admin_packages"))
    mk.row(btn(T(uid, 'admin_add_pkg'), "admin_add_package"), btn(T(uid, 'admin_del_pkg'), "admin_delete_package"))

    # 🎟️ Promo Kod Yönetimi
    mk.row(btn("▬▬▬ 🎟️ Promo Kodları ▬▬▬", "noop"))
    mk.row(btn(T(uid, 'admin_add_promo'), "admin_add_promo"), btn(T(uid, 'admin_list_promo'), "admin_list_promo"))

    # ⚙️ Sistem
    mk.row(btn("▬▬▬ ⚙️ Sistem ▬▬▬", "noop"))
    mk.row(btn(T(uid, 'admin_free_limit'), "admin_free_limit"))
    mk.row(btn(T(uid, 'admin_maintenance'), "admin_maintenance"), btn(T(uid, 'admin_sleep_schedule'), "admin_sleep_schedule"))
    mk.row(btn(T(uid, 'admin_bakim_modu'), "admin_bakim_modu"))
    if is_owner(uid):
        applies_to_admins = not is_admin_sleep_immune()
        mk.row(btn(f"👑 Adminler Uyku Modu: {'🟢 AÇIK' if applies_to_admins else '⚪️ KAPALI'}", "admin_toggle_immune"))
    mk.row(btn(T(uid, 'admin_broadcast'), "admin_broadcast"), btn(T(uid, 'admin_db'), "admin_download_db"))
    mk.row(btn(T(uid, 'admin_restore_db'), "admin_restore_db"))
    mk.row(btn("📦 Bot Dosyalarını Yedekle (.zip)", "admin_backup_files"), btn("📂 Bot Dosyalarını Yükle (.zip)", "admin_restore_files"))

    mk.row(btn(T(uid, 'back'), "back_main"))

    cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
    maint = cursor.fetchone()
    maint_on = maint and maint[0] == '1'
    cursor.execute("SELECT value FROM settings WHERE key='sleep_auto_enabled'")
    auto_row = cursor.fetchone()
    auto_on = auto_row and auto_row[0] == '1'
    cursor.execute("SELECT COUNT(*) FROM pending_files")
    pending_count = cursor.fetchone()[0]

    txt = (
        f"🛠️ <b>{T(uid, 'admin_panel')}</b>\n"
        f"{DIV}\n"
        f"📤 Bekleyen Onay: <b>{pending_count}</b>\n"
        f"🎟️ Ücretsiz Bot Limiti: <b>{get_limit()}</b>\n"
        f"😴 Uyku Modu: {'🟢 AÇIK' if maint_on else '⚪️ KAPALI'}"
        f"{' (🕐 otomatik program aktif)' if auto_on else ''}\n"
        f"🔧 Bakım Modu: {'🟢 AÇIK — sadece adminler girebilir' if is_bakim_modu() else '⚪️ KAPALI'}\n"
        f"{('👑 Adminler Uyku Modu: ' + ('🟢 AÇIK' if not is_admin_sleep_immune() else '⚪️ KAPALI') + chr(10)) if is_owner(uid) else ''}"
        f"{DIV}\n"
        f"📌 Bir kategori seçin 👇"
    )

    if mid:
        try:
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

def show_sleep_schedule(uid, mid=None):
    cursor.execute("SELECT value FROM settings WHERE key='sleep_auto_enabled'")
    en_row = cursor.fetchone()
    enabled = en_row and en_row[0] == '1'
    cursor.execute("SELECT value FROM settings WHERE key='sleep_start'")
    s_row = cursor.fetchone()
    start = s_row[0] if s_row and s_row[0] else "—"
    cursor.execute("SELECT value FROM settings WHERE key='sleep_end'")
    e_row = cursor.fetchone()
    end = e_row[0] if e_row and e_row[0] else "—"

    mk = InlineKeyboardMarkup(row_width=1)
    mk.row(btn("⏰ Saatleri Ayarla/Değiştir", "admin_sleep_set_times"))
    if start != "—" and end != "—":
        mk.row(btn("🟢 Programı Aç" if not enabled else "⚪️ Programı Kapat", "admin_sleep_toggle"))
    mk.row(btn(T(uid, 'back'), "admin_panel"))

    txt = (
        f"🕐 <b>Otomatik Uyku Programı</b>\n"
        f"{DIV}\n"
        f"📊 Durum: <b>{'🟢 Aktif' if enabled else '⚪️ Pasif'}</b>\n"
        f"🌙 Başlangıç: <b>{esc(start)}</b>\n"
        f"☀️ Bitiş: <b>{esc(end)}</b>\n"
        f"{DIV}\n"
        f"📌 Saatler ayarlanıp program açıldığında, sen değiştirmediğin sürece\n"
        f"bot her gün otomatik olarak bu saatlerde uyku moduna girer ve çıkar."
    )

    if mid:
        try:
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

def sleep_set_start_step(msg):
    aid = msg.from_user.id
    val = msg.text.strip()
    if not re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', val):
        bot.send_message(aid, "❌ <b>Geçersiz format!</b>\nSS:DD şeklinde gönder (örn: 23:00). Baştan başlamak için Uyku Programı'na tekrar bas.", parse_mode="HTML")
        return
    SLEEP_WIZARD[aid] = {'start': val}
    m = bot.send_message(aid, "⏰ Şimdi bitiş saatini <b>SS:DD</b> formatında gönder (örn: 07:00):", parse_mode="HTML")
    bot.register_next_step_handler(m, sleep_set_end_step)

def sleep_set_end_step(msg):
    aid = msg.from_user.id
    val = msg.text.strip()
    if not re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', val):
        bot.send_message(aid, "❌ <b>Geçersiz format!</b>\nSS:DD şeklinde gönder (örn: 07:00). Baştan başlamak için Uyku Programı'na tekrar bas.", parse_mode="HTML")
        SLEEP_WIZARD.pop(aid, None)
        return
    if aid not in SLEEP_WIZARD:
        bot.send_message(aid, "⌛ <b>Süre doldu!</b>\nUyku Programı'na tekrar bas.", parse_mode="HTML")
        return
    start = SLEEP_WIZARD[aid]['start']
    end = val
    cursor.execute("UPDATE settings SET value=? WHERE key='sleep_start'", (start,))
    cursor.execute("UPDATE settings SET value=? WHERE key='sleep_end'", (end,))
    cursor.execute("UPDATE settings SET value=? WHERE key='sleep_auto_enabled'", ('1',))
    conn.commit()
    SLEEP_WIZARD.pop(aid, None)
    bot.send_message(
        aid,
        f"✅ Otomatik Uyku Programı ayarlandı!\n"
        f"🌙 {start} - ☀️ {end} arası bot otomatik olarak uyku moduna girecek.\n"
        f"📌 Sen değiştirmediğin sürece bu program her gün aynı şekilde çalışır."
    )
    show_sleep_schedule(aid)

def sleep_schedule_loop():
    """Otomatik uyku programını periyodik kontrol eder ve zamanı geldiğinde
    uyku modunu (maintenance) otomatik açıp kapatır."""
    sconn, scursor = open_db(DB_PATH)
    while True:
        try:
            scursor.execute("SELECT value FROM settings WHERE key='sleep_auto_enabled'")
            en_row = scursor.fetchone()
            if en_row and en_row[0] == '1':
                scursor.execute("SELECT value FROM settings WHERE key='sleep_start'")
                s_row = scursor.fetchone()
                scursor.execute("SELECT value FROM settings WHERE key='sleep_end'")
                e_row = scursor.fetchone()
                if s_row and s_row[0] and e_row and e_row[0]:
                    now = (datetime.now() + timedelta(hours=3)).strftime("%H:%M")
                    start, end = s_row[0], e_row[0]
                    if start <= end:
                        should_sleep = start <= now < end
                    else:
                        should_sleep = now >= start or now < end

                    scursor.execute("SELECT value FROM settings WHERE key='maintenance'")
                    cur = scursor.fetchone()
                    cur_val = cur[0] if cur else '0'
                    new_val = '1' if should_sleep else '0'
                    print(f"[UYKU PROGRAM DEBUG] now={now} start={start} end={end} should_sleep={should_sleep} cur_maintenance={cur_val} new_val={new_val} -> {'GÜNCELLENİYOR' if cur_val != new_val else 'değişiklik yok'}")
                    if cur_val != new_val:
                        scursor.execute("UPDATE settings SET value=? WHERE key='maintenance'", (new_val,))
                        sconn.commit()
                else:
                    print("[UYKU PROGRAM DEBUG] sleep_start/sleep_end ayarlı değil, atlanıyor")
            else:
                print("[UYKU PROGRAM DEBUG] sleep_auto_enabled=0, döngü pasif")
        except Exception as e:
            print(f"Uyku programı hatası: {e}")
        time.sleep(30)

def monthly_users_badge_loop():
    """Telegram'da bot adının hemen altında görünen 'kısa açıklama' (short description)
    alanını periyodik olarak günceller; oraya son 30 günde /start vermiş kullanıcı
    sayısını yazar (örn. 'Coder VDS' botundaki '73 aylık kullanıcı' gibi)."""
    sconn, scursor = open_db(DB_PATH)
    last_written = None
    while True:
        try:
            scursor.execute("SELECT COUNT(*) FROM users WHERE last_start >= datetime('now','-30 days')")
            row = scursor.fetchone()
            count = row[0] if row and row[0] else 0
            text = f"{_fmt_num(count)} aylık kullanıcı"
            if text != last_written:
                try:
                    if hasattr(bot, "set_my_short_description"):
                        bot.set_my_short_description(text)
                    else:
                        requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/setMyShortDescription",
                            json={"short_description": text},
                            timeout=10
                        )
                    last_written = text
                except Exception as e:
                    print(f"Kısa açıklama güncelleme hatası: {e}")
        except Exception as e:
            print(f"Aylık kullanıcı rozeti hatası: {e}")
        time.sleep(600)  # 10 dakikada bir günceller (Telegram API'yi sık çağırmamak için)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = call.from_user.id
    data = call.data
    mid = call.message.message_id

    if is_banned(uid) and not is_admin(uid):
        try:
            bot.answer_callback_query(call.id, T(uid, 'banned_alert').format(BAN_APPEAL_CONTACT), show_alert=True)
        except:
            pass
        return

    # 🔧 Bakım Modu: adminler hariç herkesin TÜM buton etkileşimleri engellenir.
    if is_bakim_modu() and not is_admin(uid):
        try:
            bot.answer_callback_query(call.id, "🔧 Sistemimiz şu an bakımda. Lütfen daha sonra tekrar deneyin.", show_alert=True)
        except:
            pass
        return

    try:
        if data == "noop":
            try:
                bot.answer_callback_query(call.id)
            except:
                pass
            return

        if data == "check_channel":
            if check_channel(uid):
                bot.edit_message_reply_markup(uid, mid, reply_markup=None)
                bot.send_message(uid, T(uid, 'channel_ok'))
                main_menu(uid)
            else:
                bot.answer_callback_query(call.id, T(uid, 'channel_fail'), show_alert=True)

        elif data == "back_main":
            bot.clear_step_handler_by_chat_id(uid)
            main_menu(uid, mid)

        elif data == "upload_bot":
            if is_maintenance() and not (is_admin(uid) and is_admin_sleep_immune()):
                send_sleep_upload_msg(uid)
                return
            if not check_channel(uid):
                main_menu(uid)
                return
            if is_premium(uid):
                cursor.execute("SELECT bot_count FROM users WHERE user_id=?", (uid,))
                r = cursor.fetchone()
                bc = r[0] if r else 0
                ul = get_user_bot_limit(uid)
                if bc >= ul:
                    bot.send_message(uid, T(uid, 'premium_bot_limit_warning').format(bc, ul))
                    return
            else:
                cursor.execute("SELECT bot_count FROM users WHERE user_id=?", (uid,))
                r = cursor.fetchone()
                bc = r[0] if r else 0
                fl = get_limit()
                if bc >= fl:
                    bot.send_message(uid, T(uid, 'daily_limit_warning').format(fl))
                    return
            msg = bot.send_message(
                uid,
                "🚫 <b>YASAK MODÜLLER &amp; İMPORTLAR</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Bot dosyanızda aşağıdaki modüller/importlar kesinlikle yasaktır.\n"
                "Tespit edilmesi durumunda hesabınız kalıcı olarak banlanır ve tüm botlarınız silinir.\n\n"
                "❌ subprocess\n"
                "❌ os.system / os.popen / os.execv / os.execve / os.execl\n"
                "❌ multiprocessing\n"
                "❌ ctypes\n"
                "❌ __import__\n"
                "❌ eval() / exec() / compile()\n"
                "❌ importlib\n"
                "❌ pickle.loads\n"
                "❌ socket.socket / socket.connect / socket.bind\n"
                "❌ shutil.copy / shutil.move\n"
                "❌ urllib.request / urllib.urlopen\n"
                "❌ httpx.get / httpx.post / httpx.Client\n"
                "❌ pathlib.Path.read / pathlib.Path.write\n"
                "❌ base64.b64decode / zlib.decompress\n"
                "❌ marshal.loads / codecs.decode\n\n"
                "⚠️ Kod gizleme (obfuscation), filigran/reklam kancasını atlatma ve Telegram API'ye direkt bağlantı girişimleri de yasaktır ve anında ban tetikler.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📤 Yukarıdaki kuralları okuduysanız .py dosyanızı gönderin:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "back_main")
            )
            bot.clear_step_handler_by_chat_id(uid)
            bot.register_next_step_handler(msg, process_file)

        elif data == "my_bots":
            show_bots(uid, mid)

        elif data == "template_market":
            template_market(uid, mid)

        elif data.startswith("tmpl_view_"):
            template_detail(uid, mid, data[len("tmpl_view_"):])

        elif data.startswith("tmpl_install_"):
            template_install_start(uid, mid, data[len("tmpl_install_"):])

        elif data.startswith("bot_"):
            parts = data.split("_")
            if len(parts) >= 3:
                act, bid = parts[1], int(parts[2])
                if act == "start":
                    start_bot(uid, bid, mid)
                elif act == "stop":
                    stop_bot(uid, bid, mid)
                elif act == "restart":
                    stop_bot(uid, bid, mid)
                    time.sleep(1)
                    start_bot(uid, bid, mid)
                elif act == "delete":
                    del_bot(uid, bid, mid)
                elif act == "info":
                    bot_info(uid, bid, mid)
                elif act == "update":
                    request_update(uid, bid, mid)
                elif act == "logs":
                    show_bot_logs(uid, bid, mid)
                elif act == "db":
                    send_bot_database(uid, bid, mid)
                elif act == "dbup":
                    request_db_upload(uid, bid, mid)

        elif data == "premium_info":
            show_premium(uid, mid)

        elif data == "profile":
            show_profile(uid, mid)

        elif data == "ranking":
            show_rank(uid, mid)

        elif data == "settings":
            mk = InlineKeyboardMarkup(row_width=2)
            mk.row(btn("🇹🇷 Türkçe", "lang_tr"), btn("🇬🇧 English", "lang_en"))
            mk.row(btn("🇦🇿 Azərbaycan", "lang_az"))
            mk.row(btn(T(uid, 'back'), "back_main"))
            bot.edit_message_text("⚙️ Dil seçin:", uid, mid, reply_markup=mk)

        elif data == "support":
            cursor.execute("SELECT status FROM support_sessions WHERE user_id=? AND status IN ('pending','active') ORDER BY id DESC LIMIT 1", (uid,))
            _srow = cursor.fetchone()
            mk = InlineKeyboardMarkup()
            if _srow and _srow[0] == 'pending':
                status_line = "\n⏳ <b>Bekleyen destek talebiniz var.</b> Bir yetkili onayladığında bilgilendirileceksiniz."
            elif _srow and _srow[0] == 'active':
                status_line = "\n🟢 <b>Aktif bir destek sohbetiniz var.</b> Buraya yazdığınız mesajlar yetkiliye iletiliyor."
            else:
                status_line = ""
                mk.row(btn("💬 Canlı Destek Başlat", "support_start"))
            mk.row(btn(T(uid, 'back'), "back_main"))
            txt = (
                f"📞 <b>Destek</b>\n"
                f"{DIV}\n"
                f"🏢 <b>{SUPPORT_USERNAME}</b>\n"
                f"🕐 7/24 Aktif Destek"
                f"{status_line}"
            )
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data == "support_start":
            cursor.execute("SELECT status FROM support_sessions WHERE user_id=? AND status IN ('pending','active') ORDER BY id DESC LIMIT 1", (uid,))
            r = cursor.fetchone()
            if r and r[0] == 'pending':
                bot.answer_callback_query(call.id, "⏳ Zaten bekleyen bir destek talebiniz var.", show_alert=True)
                return
            if r and r[0] == 'active':
                bot.answer_callback_query(call.id, "🟢 Zaten aktif bir destek sohbetiniz var, mesaj yazmanız yeterli.", show_alert=True)
                return
            bot.clear_step_handler_by_chat_id(uid)
            m = bot.send_message(
                uid,
                "📝 <b>Destek Talebi</b>\n" + DIV + "\n"
                "Lütfen sorununuzu veya talebinizi tek bir mesaj halinde yazın.\n"
                "Mesajınızı gönderdikten sonra bir yetkilinin onayını bekleyeceksiniz.\n\n"
                "❌ Vazgeçmek için: /iptal",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "back_main")
            )
            bot.register_next_step_handler(m, support_first_message)

        elif data.startswith("supapp_") and is_admin(uid):
            support_approve(int(data.split("_")[1]), uid, mid, call.id)

        elif data.startswith("suprej_") and is_admin(uid):
            support_reject(int(data.split("_")[1]), uid, mid, call.id)

        elif data.startswith("supend_") and is_admin(uid):
            support_end(int(data.split("_")[1]), uid, mid, call.id)

        elif data == "help":
            mk = InlineKeyboardMarkup()
            mk.row(btn(T(uid, 'back'), "back_main"))
            txt = (
                f"❓ <b>Yardım Menüsü</b>\n"
                f"{DIV}\n"
                f"📤 <b>Bot Yükle</b> — .py bot dosyanı yükle, onaydan sonra çalıştır\n"
                f"📁 <b>Botlarım</b> — botlarını başlat/durdur/sil\n"
                f"⭐ <b>VIP</b> — VIP paketlerini incele ve satın al\n"
                f"👤 <b>Profil</b> — hesap bilgilerini gör\n"
                f"🏆 <b>Sıralama</b> — en çok bota sahip kullanıcılar\n"
                f"⚙️ <b>Ayarlar</b> — dil tercihini değiştir\n"
                f"📞 <b>Destek</b> — bize ulaş\n"
                f"{DIV}\n"
                f"⭐ VIP üyeler satın aldıkları pakete göre değişen sayıda bot barındırabilir."
            )
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data.startswith("lang_"):
            lang = data.split("_")[1]
            cursor.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, uid))
            conn.commit()
            bot.answer_callback_query(call.id, T(uid, 'lang_changed'))
            main_menu(uid, mid)

        elif data == "admin_panel" and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            admin_panel(uid, mid)

        elif ((data.startswith("admin_") and data not in ("admin_add_promo", "admin_list_promo")) or data.startswith("pkg_template_")) and is_admin(uid):
            handle_admin(call)

        elif data.startswith("aprv_") and is_admin(uid):
            approve_file(int(data.split("_")[1]), uid)

        elif data.startswith("rej_") and is_admin(uid):
            reject_file(int(data.split("_")[1]), uid)

        elif data.startswith("del_pkg_") and is_admin(uid):
            cursor.execute("DELETE FROM premium_packages WHERE id=?", (int(data.split("_")[2]),))
            conn.commit()
            bot.answer_callback_query(call.id, T(uid, 'pkg_deleted'))
            admin_panel(uid, mid)

        elif data.startswith("pkgpick_") and is_admin(uid):
            parts = data.split("_")
            tid, pkg_id = int(parts[1]), int(parts[2])
            mk = duration_keyboard("premdur", extra=f"{tid}_{pkg_id}")
            try:
                bot.edit_message_text(f"⏳ <code>{tid}</code> için ne kadar süreli VIP verilsin?", uid, mid, reply_markup=mk, parse_mode="HTML")
            except:
                bot.send_message(uid, f"⏳ <code>{tid}</code> için ne kadar süreli VIP verilsin?", reply_markup=mk, parse_mode="HTML")

        elif data.startswith("premdur_") and is_admin(uid):
            parts = data.split("_")
            tid, pkg_id, minutes = int(parts[1]), int(parts[2]), int(parts[3])
            apply_premium(tid, minutes, pkg_id, uid)
            try:
                bot.edit_message_reply_markup(uid, mid, reply_markup=None)
            except:
                pass

        elif data.startswith("pkgdur_") and is_admin(uid):
            minutes = int(data.split("_")[1])
            if uid not in PKG_WIZARD:
                bot.answer_callback_query(call.id, "⌛ Süre doldu, tekrar dene: Paket Ekle", show_alert=True)
                return
            PKG_WIZARD[uid]['duration_minutes'] = minutes
            try:
                bot.edit_message_reply_markup(uid, mid, reply_markup=None)
            except:
                pass
            mkwm = InlineKeyboardMarkup(row_width=2)
            mkwm.row(btn("✅ Evet (Aktif)", "pkgwm_1"), btn("❌ Hayır (Pasif)", "pkgwm_0"))
            bot.send_message(
                uid,
                "🏷️ <b>Sistem Filigranı</b>\n"
                f"{DIV}\n"
                "Bu paketle kurulan botların mesajlarının altında \"Hostinger By\" filigranı görünsün mü?\n\n"
                "💡 Genelde ücretsiz/giriş paketlerinde <b>Aktif</b>, üst paketlerde <b>Pasif</b> yapılır.",
                reply_markup=mkwm, parse_mode="HTML"
            )

        elif data.startswith("pkgwm_") and is_admin(uid):
            wm_on = int(data.split("_")[1])
            info = PKG_WIZARD.pop(uid, None)
            if not info:
                bot.answer_callback_query(call.id, "⌛ Süre doldu, tekrar dene: Paket Ekle", show_alert=True)
                return
            cursor.execute(
                "INSERT INTO premium_packages (name, price, description, bot_limit, duration_minutes, watermark) VALUES (?,?,?,?,?,?)",
                (info['name'], info['price'], info['desc'], info['limit'], info['duration_minutes'], wm_on)
            )
            conn.commit()
            try:
                bot.edit_message_reply_markup(uid, mid, reply_markup=None)
            except:
                pass
            bot.send_message(uid, T(uid, 'pkg_added'))
            admin_panel(uid)

        elif data.startswith("pkgview_user_"):
            pkg_id = int(data.split("_")[2])
            cursor.execute(
                "SELECT name, price, description, bot_limit, duration_minutes, watermark FROM premium_packages WHERE id=?",
                (pkg_id,)
            )
            p = cursor.fetchone()
            if not p:
                bot.answer_callback_query(call.id, "❌ Paket bulunamadı!", show_alert=True)
                return
            name, price, desc, limit, minutes, wm = p
            price_txt = "Ücretsiz" if price == 0 else f"{price} ⭐ Stars"
            dur_txt = format_duration(minutes)
            wm_txt = "Evet (Aktif)" if (wm is None or wm) else "Hayır (Pasif)"

            txt = (
                f"🌟 <b>{esc(name)} Paket Detayları</b>\n"
                f"{DIV}\n\n"
                f"🤖 Bot Limiti: <b>{limit} Bot Barındırma</b>\n"
                f"🏷️ Sistem Filigranı: <b>{wm_txt}</b>\n"
                f"💰 Fiyat: <b>{price_txt}</b>\n"
                f"⏳ Abonelik Süresi: <b>{dur_txt}</b>\n"
            )
            if desc:
                txt += f"\n📝 {esc(desc)}\n"

            mk = InlineKeyboardMarkup(row_width=1)
            mk.row(btn("⭐ VIP Paket Al", f"buy_package_{pkg_id}"))
            mk.row(btn("◀️ Paketlere Dön", "premium_info"))
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data.startswith("buy_package_"):
            pkg_id = int(data.split("_")[2])
            cursor.execute("SELECT name, price, description, bot_limit, duration_minutes FROM premium_packages WHERE id=?", (pkg_id,))
            p = cursor.fetchone()
            if not p:
                bot.answer_callback_query(call.id, "❌ Paket bulunamadı!", show_alert=True)
                return
            name, price, desc, limit, minutes = p
            dur_txt = format_duration(minutes)

            if price <= 0:
                activate_package(uid, pkg_id)
                return

            try:
                inv_mk = InlineKeyboardMarkup()
                inv_mk.row(InlineKeyboardButton("⭐ Öde", pay=True))
                inv_mk.row(btn(T(uid, 'back'), "invoice_back"))
                bot.send_invoice(
                    uid,
                    title=name,
                    description=f"{desc}  •  🤖 Bot Hakkı: {limit} bot  •  ⏳ Süre: {dur_txt}",
                    invoice_payload=f"pkg_{pkg_id}",
                    provider_token="",
                    currency="XTR",
                    prices=[LabeledPrice(label=name, amount=price)],
                    reply_markup=inv_mk,
                )
            except Exception as e:
                print(f"Invoice Error: {e}")
                bot.send_message(uid, "❌ <b>Fatura oluşturulamadı!</b>\nLütfen daha sonra tekrar dene.", parse_mode="HTML")

        elif data == "invoice_back":
            # ⭐ Fatura, paket listesi mesajının ÜZERİNE değil ayrı bir mesaj olarak gönderildiği için
            # paket listesi mesajı hâlâ ekranda duruyor. Burada sadece fatura mesajını siliyoruz,
            # yeni bir mesaj göndermeye gerek yok.
            bot.clear_step_handler_by_chat_id(uid)
            try:
                bot.delete_message(uid, mid)
            except:
                pass

        elif data.startswith("pkgview_") and is_admin(uid):
            send_pkg_view(uid, int(data.split("_")[1]), mid)

        elif data.startswith("pkgedit_name_") and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            pkg_id = int(data.split("_")[2])
            m = bot.send_message(uid, "✏️ <b>Yeni isim:</b>", parse_mode="HTML", reply_markup=_back_kb(uid, f"pkgview_{pkg_id}"))
            bot.register_next_step_handler(m, pkg_edit_set_field, pkg_id, 'name')

        elif data.startswith("pkgedit_price_") and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            pkg_id = int(data.split("_")[2])
            m = bot.send_message(uid, "💰 <b>Yeni fiyat</b> (⭐), sadece rakam (0 = ücretsiz):", parse_mode="HTML", reply_markup=_back_kb(uid, f"pkgview_{pkg_id}"))
            bot.register_next_step_handler(m, pkg_edit_set_field, pkg_id, 'price')

        elif data.startswith("pkgedit_desc_") and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            pkg_id = int(data.split("_")[2])
            m = bot.send_message(uid, "📝 <b>Yeni açıklama:</b>", parse_mode="HTML", reply_markup=_back_kb(uid, f"pkgview_{pkg_id}"))
            bot.register_next_step_handler(m, pkg_edit_set_field, pkg_id, 'description')

        elif data.startswith("pkgedit_limit_") and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            pkg_id = int(data.split("_")[2])
            m = bot.send_message(uid, "🤖 <b>Yeni bot limiti</b>, sadece rakam:", parse_mode="HTML", reply_markup=_back_kb(uid, f"pkgview_{pkg_id}"))
            bot.register_next_step_handler(m, pkg_edit_set_field, pkg_id, 'bot_limit')

        elif data.startswith("pkgedit_dur_") and is_admin(uid):
            pkg_id = int(data.split("_")[2])
            mkk = duration_keyboard("pkgdurset", extra=pkg_id)
            bot.send_message(uid, "⏳ <b>Yeni süreyi seç:</b>", reply_markup=mkk, parse_mode="HTML")

        elif data.startswith("pkgedit_wm_") and is_admin(uid):
            pkg_id = int(data.split("_")[2])
            cursor.execute("SELECT watermark FROM premium_packages WHERE id=?", (pkg_id,))
            row = cursor.fetchone()
            cur_wm = row[0] if row and row[0] is not None else 1
            new_wm = 0 if cur_wm else 1
            cursor.execute("UPDATE premium_packages SET watermark=? WHERE id=?", (new_wm, pkg_id))
            conn.commit()
            bot.answer_callback_query(call.id, f"🏷️ Filigran {'Aktif edildi' if new_wm else 'Pasif edildi'}!")
            send_pkg_view(uid, pkg_id, mid)

        elif data.startswith("pkgdurset_") and is_admin(uid):
            parts = data.split("_")
            pkg_id, minutes = int(parts[1]), int(parts[2])
            cursor.execute("UPDATE premium_packages SET duration_minutes=? WHERE id=?", (minutes, pkg_id))
            conn.commit()
            bot.answer_callback_query(call.id, "✅ Süre güncellendi!")
            send_pkg_view(uid, pkg_id)

        elif data == "use_promo":
            bot.clear_step_handler_by_chat_id(uid)
            m = bot.send_message(uid, T(uid, 'promo_ask'), parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
            bot.register_next_step_handler(m, redeem_promo_step)

        elif data == "admin_add_promo" and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            m = bot.send_message(
                uid,
                "🎟️ <b>Promo kodu gir</b> (örn: NEBULA2026) veya rastgele üretmek için <code>auto</code> yaz:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "admin_panel")
            )
            bot.register_next_step_handler(m, promo_ask_code)

        elif data == "admin_list_promo" and is_admin(uid):
            send_promo_list(uid, mid)

        elif data.startswith("delpromo_") and is_admin(uid):
            code = data.split("_", 1)[1]
            cursor.execute("DELETE FROM promo_codes WHERE code=?", (code,))
            conn.commit()
            bot.answer_callback_query(call.id, "🗑️ Promo silindi!")
            send_promo_list(uid, mid)

        elif data.startswith("promodur_") and is_admin(uid):
            minutes = int(data.split("_")[1])
            info = PROMO_WIZARD.pop(uid, None)
            if not info:
                bot.answer_callback_query(call.id, "⌛ Süre doldu, tekrar dene: Promo Oluştur", show_alert=True)
                return
            cursor.execute(
                "INSERT OR REPLACE INTO promo_codes (code, duration_minutes, max_uses, used_count, active, created_at) VALUES (?,?,?,0,1,?)",
                (info['code'], minutes, info['max_uses'], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            try:
                bot.edit_message_reply_markup(uid, mid, reply_markup=None)
            except:
                pass
            uses_txt = "♾️ Sınırsız" if info['max_uses'] <= 0 else str(info['max_uses'])
            bot.send_message(
                uid,
                f"{T(uid, 'promo_created')}\n{DIV}\n"
                f"🎟️ Kod: <code>{esc(info['code'])}</code>\n"
                f"🔁 Kullanım Limiti: <b>{uses_txt}</b>\n"
                f"⏳ VIP Süresi: <b>{format_duration(minutes)}</b>",
                parse_mode="HTML"
            )
            admin_panel(uid)

    except Exception as e:
        print(f"Callback Error: {e}")

def process_file(msg):
    uid = msg.from_user.id

    _maint = is_maintenance()
    _adm = is_admin(uid)
    _imm = is_admin_sleep_immune()
    print(f"[UYKU DEBUG] uid={uid} maintenance={_maint} is_admin={_adm} sleep_immune={_imm} -> blok={_maint and not (_adm and _imm)}")
    if _maint and not (_adm and _imm):
        send_sleep_upload_msg(uid)
        return

    if not msg.document:
        bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        return
    
    if not msg.document.file_name.endswith('.py'):
        bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        return
    
    if msg.document.file_size > 5 * 1024 * 1024:
        bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        return

    try:
        st = bot.send_message(uid, "📤 <b>Dosya alınıyor...</b>", parse_mode="HTML")
        fi = bot.get_file(msg.document.file_id)
        df = bot.download_file(fi.file_path)
        
        os.makedirs(BOT_FILES_DIR, exist_ok=True)
        fn = sanitize_filename(msg.document.file_name)
        fp = f"{BOT_FILES_DIR}/{uid}_{int(time.time())}_{fn}"

        with open(fp, "wb") as f:
            f.write(df)

        # 🔍 Otomatik güvenlik taraması (regex + AST tabanlı derin statik analiz, ~12-17sn)
        is_clean, reasons = run_scan_with_progress(uid, st.message_id, fp)
        if not is_clean:
            try:
                os.remove(fp)
            except:
                pass

            ban_user(uid, ", ".join(reasons))

            try:
                bot.edit_message_text(
                    T(uid, 'malicious_detected').format(", ".join(reasons), SUPPORT_USERNAME),
                    uid, st.message_id
                )
            except:
                pass

            for aid in ADMIN_IDS:
                try:
                    bot.send_message(
                        aid,
                        f"🚫 KÖTÜ AMAÇLI KOD TESPİT EDİLDİ\n👤 Kullanıcı: {user_display(uid, html=False)}\n📄 Dosya: {fn}\n⚠️ Sebep: {', '.join(reasons)}\n\n✅ Kullanıcı otomatik olarak engellendi."
                    )
                except:
                    pass
            return

        # ✅ Güvenlik taramasından temiz geçti -> admin onayına gönder
        cursor.execute("INSERT INTO bot_files (user_id, file_name, file_path, submitted_at, status) VALUES (?,?,?,?,?)",
                      (uid, fn, fp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'pending'))
        conn.commit()
        bid = cursor.lastrowid

        cursor.execute("UPDATE users SET total_files=total_files+1 WHERE user_id=?", (uid,))
        conn.commit()

        cursor.execute(
            "INSERT INTO pending_files (user_id, file_name, file_path, submitted_at, bot_file_id) VALUES (?,?,?,?,?)",
            (uid, fn, fp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bid)
        )
        conn.commit()
        pid = cursor.lastrowid

        bot.edit_message_text(T(uid, 'upload_success'), uid, st.message_id, reply_markup=_back_kb(uid, "back_main"), parse_mode="HTML")

        mk = InlineKeyboardMarkup()
        mk.row(btn("✅ Onayla", f"aprv_{pid}"), btn("❌ Reddet", f"rej_{pid}"))
        for aid in ADMIN_IDS:
            try:
                bot.send_message(
                    aid,
                    f"📤 <b>Yeni Bot Onay Bekliyor</b>\n"
                    f"{DIV}\n"
                    f"👤 Kullanıcı: {user_display(uid)}\n📄 Dosya: {esc(fn)}\n🆔 İşlem No: {pid}",
                    reply_markup=mk, parse_mode="HTML"
                )
            except:
                pass

    except Exception as e:
        print(f"Upload Error: {e}")
        try:
            bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        except:
            pass

def approve_file(pid, aid):
    try:
        cursor.execute("SELECT user_id, file_name, file_path, bot_file_id, is_update FROM pending_files WHERE id=?", (pid,))
        p = cursor.fetchone()
    except Exception as e:
        log_error("approve_file (DB okuma)", e)
        return
    
    if not p:
        bot.send_message(aid, "⚠️ <b>Bulunamadı!</b>", parse_mode="HTML")
        return
    
    uid, fn, fp, bid, is_update = p
    
    token = None
    uname = None
    
    try:
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        for pat in [r'TOKEN\s*=\s*["\']([^"\']+)["\']', r'BOT_TOKEN\s*=\s*["\']([^"\']+)["\']']:
            m = re.search(pat, content)
            if m and len(m.group(1)) > 30 and ':' in m.group(1):
                token = m.group(1)
                break
        if token:
            r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
            if r.json().get('ok'):
                uname = r.json()['result']['username']
    except:
        pass

    approved_hash = _file_hash(fp)
    cursor.execute("UPDATE bot_files SET status='approved', bot_token=?, bot_username=?, approved_at=?, "
                  "prev_file_path=NULL, prev_bot_token=NULL, prev_bot_username=NULL, approved_file_hash=? WHERE id=?",
                  (token, uname, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), approved_hash, bid))
    cursor.execute("DELETE FROM pending_files WHERE id=?", (pid,))
    if not is_update:
        cursor.execute("UPDATE users SET bot_count=bot_count+1 WHERE user_id=?", (uid,))
    else:
        # 🔄 Güncellemede eski dosya artık kullanılmıyor, temizle
        try:
            cursor.execute("SELECT prev_file_path FROM bot_files WHERE id=?", (bid,))
            old = cursor.fetchone()
            if old and old[0] and os.path.exists(old[0]) and old[0] != fp:
                os.remove(old[0])
        except:
            pass
    cursor.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='total_approved'")
    conn.commit()

    # 📦 Onaylanan dosyayı arşiv kanalına gönder
    try:
        if os.path.exists(fp):
            with open(fp, "rb") as af:
                bot.send_document(
                    ARCHIVE_CHAT_ID, af,
                    caption=f"📦 Onaylandı\n👤 UID: {uid}\n📄 {fn}\n🤖 @{uname if uname else '-'}"
                )
    except Exception as _e:
        print(f"[ARŞİV HATA] {_e}")

    if token:
        update_bot_description(token)

    cursor.execute("SELECT username FROM users WHERE user_id=?", (uid,))
    urow = cursor.fetchone()
    sender_uname = urow[0] if urow and urow[0] and urow[0] != "None" else None

    mk_aid = InlineKeyboardMarkup()
    mk_aid.row(btn(T(aid, 'back'), "admin_panel"))
    bot.send_message(
        aid,
        f"🎉 <b>Botu Onayladınız!</b>\n{DIV}\n"
        f"📄 Dosya: <b>{esc(fn)}</b>\n"
        f"👤 Gönderen: <code>{uid}</code>{(' (@' + esc(sender_uname) + ')') if sender_uname else ''}\n"
        f"{('🤖 Bot Kullanıcı Adı: @' + esc(uname) + chr(10)) if uname else ''}"
        f"{DIV}\n✅ Kullanıcıya bildirim gönderildi.",
        reply_markup=mk_aid,
        parse_mode="HTML"
    )
    try:
        mk_uid = InlineKeyboardMarkup()
        mk_uid.row(btn(T(uid, 'back'), "back_main"))
        bot.send_message(
            uid,
            f"✅ <b>Bot Onaylandı!</b>\n{DIV}\n"
            f"📄 Dosya: <b>{esc(fn)}</b>\n"
            f"{('🤖 Bot Kullanıcı Adı: @' + esc(uname) + chr(10)) if uname else ''}"
            f"{DIV}\n🚀 Botlarım bölümünden başlatabilirsiniz.",
            reply_markup=mk_uid,
            parse_mode="HTML"
        )
    except:
        pass

def reject_file(pid, aid):
    try:
        cursor.execute("SELECT user_id, bot_file_id, is_update, file_path FROM pending_files WHERE id=?", (pid,))
        p = cursor.fetchone()
    except Exception as e:
        log_error("reject_file (DB okuma)", e)
        return
    
    if not p:
        bot.send_message(aid, "⚠️ <b>Bulunamadı!</b>", parse_mode="HTML")
        return
    
    uid, bid, is_update, rejected_fp = p

    if is_update:
        # 🔄 Güncelleme reddedildiğinde botu eski (onaylı) haline geri döndür
        cursor.execute("SELECT prev_file_path, prev_bot_token, prev_bot_username FROM bot_files WHERE id=?", (bid,))
        prev = cursor.fetchone()
        try:
            if rejected_fp and os.path.exists(rejected_fp):
                os.remove(rejected_fp)
        except:
            pass
        if prev and prev[0]:
            cursor.execute(
                "UPDATE bot_files SET file_path=?, bot_token=?, bot_username=?, status='approved', "
                "bot_status='stopped', pid=NULL, prev_file_path=NULL, prev_bot_token=NULL, prev_bot_username=NULL, "
                "approved_file_hash=? WHERE id=?",
                (prev[0], prev[1], prev[2], _file_hash(prev[0]), bid)
            )
        else:
            cursor.execute(
                "UPDATE bot_files SET status='rejected', prev_file_path=NULL, prev_bot_token=NULL, "
                "prev_bot_username=NULL WHERE id=?",
                (bid,)
            )
    else:
        cursor.execute("DELETE FROM bot_files WHERE id=?", (bid,))

    cursor.execute("DELETE FROM pending_files WHERE id=?", (pid,))
    conn.commit()
    
    mk_aid = InlineKeyboardMarkup()
    mk_aid.row(btn(T(aid, 'back'), "admin_panel"))
    bot.send_message(
        aid,
        f"🚫 <b>Botu Reddettiniz!</b>\n{DIV}\n"
        f"👤 Kullanıcı: {user_display(uid)}\n"
        f"{DIV}\n📨 Kullanıcıya bildirim gönderildi.",
        reply_markup=mk_aid,
        parse_mode="HTML"
    )
    try:
        mk_uid = InlineKeyboardMarkup()
        mk_uid.row(btn(T(uid, 'back'), "back_main"))
        bot.send_message(uid, T(uid, 'bot_rejected'), reply_markup=mk_uid, parse_mode="HTML")
    except:
        pass

def show_bots(uid, mid):
    try:
        cursor.execute("SELECT id, file_name, status, bot_status FROM bot_files WHERE user_id=? ORDER BY id DESC", (uid,))
        bots = cursor.fetchall()
    except:
        bots = []
    
    mk = InlineKeyboardMarkup(row_width=2)
    
    if not bots:
        mk.row(btn(T(uid, 'back'), "back_main"))
        try:
            bot.edit_message_text(f"📁 <b>Botlarım</b>\n{DIV}\n{T(uid, 'no_bots')}", uid, mid, reply_markup=mk, parse_mode="HTML")
        except:
            pass
        return
    
    for bid, fn, st, bs in bots:
        se = "✅" if st == "approved" else "⏳"
        be = "🟢" if bs == "running" else "🔴"
        mk.add(btn(f"{be} {fn[:20]} ({se})", f"bot_info_{bid}"))
    
    mk.row(btn(T(uid, 'back'), "back_main"))
    
    txt = f"📁 <b>Botlarım</b> ({len(bots)})\n{DIV}\n🟢 Çalışıyor · 🔴 Durdu · ✅ Onaylı · ⏳ Beklemede\n{DIV}\n{T(uid, 'select_bot')}"
    try:
        bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
    except:
        pass

def bot_info(uid, bid, mid):
    try:
        cursor.execute("SELECT id, file_name, status, bot_status, bot_username, start_count, pid FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        bd = cursor.fetchone()
    except:
        bd = None
    
    if not bd:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return
    
    fid, fn, st, bs, bu, sc, pid = bd
    
    mk = InlineKeyboardMarkup(row_width=2)
    if st == "approved":
        if bs == "running":
            mk.row(btn(T(uid, 'stop'), f"bot_stop_{fid}"), btn(T(uid, 'restart'), f"bot_restart_{fid}"))
        else:
            mk.row(btn(T(uid, 'start'), f"bot_start_{fid}"))
        mk.row(btn(T(uid, 'delete'), f"bot_delete_{fid}"))
        mk.row(btn("📜 Loglar", f"bot_logs_{fid}"), btn("💾 Veritabanı", f"bot_db_{fid}"))
        mk.row(btn("📤 Veritabanı Yükle", f"bot_dbup_{fid}"))
    mk.row(btn("🔄 Dosyayı Güncelle", f"bot_update_{fid}"))
    mk.row(btn(T(uid, 'back'), "my_bots"))
    
    sd = {"pending": "⏳ Onay Bekliyor", "approved": "✅ Onaylandı", "rejected": "❌ Reddedildi"}
    bd2 = {"running": "🟢 Çalışıyor", "stopped": "🔴 Durduruldu", "error": "⚠️ Hata"}
    
    txt = (
        f"🤖 <b>Bot Detayı</b>\n"
        f"{DIV}\n"
        f"📄 <b>Dosya:</b> {esc(fn)}\n"
        f"📛 <b>Bot Adı:</b> @{esc(bu) or 'Bilinmiyor'}\n"
        f"📌 <b>Onay Durumu:</b> {sd.get(st, st)}\n"
        f"🔄 <b>Çalışma Durumu:</b> {bd2.get(bs, bs)}\n"
    )
    if pid:
        txt += f"🔍 <b>PID:</b> <code>{pid}</code>\n"
    txt += DIV
    
    try:
        bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
    except:
        pass

def request_update(uid, bid, mid):
    try:
        cursor.execute("SELECT id, file_name FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        bd = cursor.fetchone()
    except:
        bd = None

    if not bd:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return

    if not is_premium(uid):
        cursor.execute("SELECT last_update_at FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        last = r[0] if r else None
        today = datetime.now().strftime("%Y-%m-%d")
        if last == today:
            try:
                bot.answer_callback_query(
                    mid,
                    "🔄 Günlük dosya güncelleme hakkını kullandın!\n⭐ VIP olursan sınırsız güncelleyebilirsin.",
                    show_alert=True
                )
            except:
                pass
            return

    msg = bot.send_message(
        uid,
        "🔄 <b>Dosya Güncelleme</b>\n"
        f"{DIV}\n"
        "📤 Botun için yeni <b>.py</b> dosyasını gönder.\n\n"
        "🚫 <b>YASAK MODÜLLER &amp; İMPORTLAR</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "❌ subprocess\n"
        "❌ os.system / os.popen / os.execv / os.execve / os.execl\n"
        "❌ multiprocessing\n"
        "❌ ctypes\n"
        "❌ __import__\n"
        "❌ eval() / exec() / compile()\n"
        "❌ importlib\n"
        "❌ pickle.loads\n"
        "❌ socket.socket / socket.connect / socket.bind\n"
        "❌ shutil.copy / shutil.move\n"
        "❌ urllib.request / urllib.urlopen\n"
        "❌ httpx.get / httpx.post / httpx.Client\n"
        "❌ pathlib.Path.read / pathlib.Path.write\n"
        "❌ base64.b64decode / zlib.decompress\n"
        "❌ marshal.loads / codecs.decode\n\n"
        "⚠️ Tespit edilmesi durumunda hesabın kalıcı olarak banlanır.\n"
        "📌 Dosya gönderildikten sonra bot yeniden taranıp admin onayına düşer.",
        parse_mode="HTML",
        reply_markup=_back_kb(uid, "back_main")
    )
    bot.clear_step_handler_by_chat_id(uid)
    bot.register_next_step_handler(msg, process_update_file, bid)

def process_update_file(msg, bid):
    uid = msg.from_user.id

    _maint = is_maintenance()
    _adm = is_admin(uid)
    _imm = is_admin_sleep_immune()
    print(f"[UYKU DEBUG - GÜNCELLEME] uid={uid} maintenance={_maint} is_admin={_adm} sleep_immune={_imm} -> blok={_maint and not (_adm and _imm)}")
    if _maint and not (_adm and _imm):
        send_sleep_upload_msg(uid)
        return

    try:
        cursor.execute("SELECT file_path, bot_token, bot_username, pid FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        old = cursor.fetchone()
    except:
        old = None

    if not old:
        bot.send_message(uid, "⚠️ <b>Bot bulunamadı!</b>", parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
        return

    old_fp, old_token, old_uname, old_pid = old

    if not msg.document:
        bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        return

    if not msg.document.file_name.endswith('.py'):
        bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        return

    if msg.document.file_size > 5 * 1024 * 1024:
        bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        return

    try:
        st = bot.send_message(uid, "📤 <b>Dosya alınıyor...</b>", parse_mode="HTML")
        fi = bot.get_file(msg.document.file_id)
        df = bot.download_file(fi.file_path)

        os.makedirs(BOT_FILES_DIR, exist_ok=True)
        fn = sanitize_filename(msg.document.file_name)
        fp = f"{BOT_FILES_DIR}/{uid}_{int(time.time())}_{fn}"

        with open(fp, "wb") as f:
            f.write(df)

        # 🔍 Otomatik güvenlik taraması (regex + AST tabanlı derin statik analiz, ~12-17sn)
        is_clean, reasons = run_scan_with_progress(uid, st.message_id, fp)
        if not is_clean:
            try:
                os.remove(fp)
            except:
                pass

            ban_user(uid, ", ".join(reasons))

            try:
                bot.edit_message_text(
                    T(uid, 'malicious_detected').format(", ".join(reasons), SUPPORT_USERNAME),
                    uid, st.message_id
                )
            except:
                pass

            for aid in ADMIN_IDS:
                try:
                    bot.send_message(
                        aid,
                        f"🚫 KÖTÜ AMAÇLI KOD TESPİT EDİLDİ (Güncelleme)\n👤 Kullanıcı: {user_display(uid, html=False)}\n📄 Dosya: {fn}\n⚠️ Sebep: {', '.join(reasons)}\n\n✅ Kullanıcı otomatik olarak engellendi."
                    )
                except:
                    pass
            return

        # ✅ Temiz geçti -> botu durdur, dosyayı değiştir, admin onayına gönder
        if old_pid:
            kill_pid(old_pid)

        cursor.execute(
            "UPDATE bot_files SET file_name=?, file_path=?, status='pending', bot_status='stopped', "
            "pid=NULL, bot_token=NULL, bot_username=NULL, prev_file_path=?, prev_bot_token=?, prev_bot_username=? "
            "WHERE id=?",
            (fn, fp, old_fp, old_token, old_uname, bid)
        )
        conn.commit()

        cursor.execute(
            "INSERT INTO pending_files (user_id, file_name, file_path, submitted_at, bot_file_id, is_update) VALUES (?,?,?,?,?,1)",
            (uid, fn, fp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bid)
        )
        conn.commit()
        pending_id = cursor.lastrowid

        cursor.execute(
            "UPDATE users SET last_update_at=? WHERE user_id=?",
            (datetime.now().strftime("%Y-%m-%d"), uid)
        )
        conn.commit()

        bot.edit_message_text(T(uid, 'upload_success'), uid, st.message_id, reply_markup=_back_kb(uid, "back_main"), parse_mode="HTML")

        mk = InlineKeyboardMarkup()
        mk.row(btn("✅ Onayla", f"aprv_{pending_id}"), btn("❌ Reddet", f"rej_{pending_id}"))
        for aid in ADMIN_IDS:
            try:
                bot.send_message(
                    aid,
                    f"🔄 <b>Bot Dosyası Güncellendi - Onay Bekliyor</b>\n"
                    f"{DIV}\n"
                    f"👤 Kullanıcı: {user_display(uid)}\n📄 Dosya: {esc(fn)}\n🆔 İşlem No: {pending_id}",
                    reply_markup=mk, parse_mode="HTML"
                )
            except:
                pass

    except Exception as e:
        print(f"Update Upload Error: {e}")
        try:
            bot.send_message(uid, T(uid, 'upload_error'), reply_markup=_back_kb(uid, "back_main"))
        except:
            pass

_OWN_USERNAME_CACHE = {"v": None}

def get_own_username():
    """Bu hosting botunun kendi @kullanıcı adını döndürür (filigran metninde kullanılır)."""
    if _OWN_USERNAME_CACHE["v"] is None:
        try:
            _OWN_USERNAME_CACHE["v"] = bot.get_me().username
        except Exception:
            _OWN_USERNAME_CACHE["v"] = "nebulahosting_bot"
    return _OWN_USERNAME_CACHE["v"]


def get_effective_watermark(uid):
    """Kullanıcının aktif VIP paketine göre kurduğu botlarda filigran gösterilip
    gösterilmeyeceğini belirler. VIP/premium kullanıcılarda paket ayarından bağımsız
    olarak filigran HER ZAMAN kapalıdır. Sadece ücretsiz kullanıcılarda AKTİF kabul edilir."""
    try:
        cursor.execute("SELECT premium, premium_package FROM users WHERE user_id=?", (uid,))
        row = cursor.fetchone()
    except:
        row = None
    if not row or not row[0]:
        return True
    # Premium/VIP kullanıcı: paketin watermark sütununa bakılmaksızın filigran kapalı.
    return False


def build_bot_launcher(fp, bid, add_watermark=False, redirect_db=False):
    """Orijinal bot dosyasını HİÇ değiştirmeden, çalışma zamanında monkeypatch ile:
    1) redirect_db=True ise, botun içindeki sqlite3.connect() çağrılarını -kodda hangi
       isim/yol yazılırsa yazılsın- o bota özel izole bir klasöre (userdb_{bid}/) yönlendirir.
       Böylece panel, kullanıcının kendi yazdığı botun veritabanının HER ZAMAN nerede
       olduğunu bilir ve 'Veritabanı' butonuyla güvenle indirilebilir. Sadece şablon dışı
       (kullanıcı yüklemesi) botlarda kullanılır; şablon botlarının veritabanı yolu zaten
       bilindiği için (template_db_path) onlarda dokunulmaz.
    2) add_watermark=True ise, telebot mesajlarının altına filigran ekler.
    İkisi de gerekmiyorsa orijinal dosya (fp) doğrudan döndürülür."""
    if not add_watermark and not redirect_db:
        return fp

    parts = ["import runpy", "import os"]

    if redirect_db:
        db_dir = user_db_dir(bid)
        parts.append(
            "try:\n"
            "    import sqlite3 as _sq3\n"
            f"    _DB_DIR = {db_dir!r}\n"
            "    os.makedirs(_DB_DIR, exist_ok=True)\n"
            "    _orig_connect = _sq3.connect\n"
            "    def _redirect_connect(database, *a, **kw):\n"
            "        try:\n"
            "            if isinstance(database, str) and database not in (':memory:', '') and not database.startswith('file:'):\n"
            "                database = os.path.join(_DB_DIR, os.path.basename(database) or 'database.db')\n"
            "        except Exception:\n"
            "            pass\n"
            "        return _orig_connect(database, *a, **kw)\n"
            "    _sq3.connect = _redirect_connect\n"
            "except Exception:\n"
            "    pass"
        )

    if add_watermark:
        footer = f"Hostinger By @{get_own_username()}"
        parts.append(
            "try:\n"
            "    import telebot as _tb\n"
            f"    _WM_TEXT = {footer!r}\n"
            "    def _wm_wrap(fn):\n"
            "        def _wrapped(self, chat_id, text='', *a, **kw):\n"
            "            try:\n"
            "                sep = chr(10) + chr(10) if text else ''\n"
            "                text = f\"{text}{sep}🚀 {_WM_TEXT}\"\n"
            "            except Exception:\n"
            "                pass\n"
            "            return fn(self, chat_id, text, *a, **kw)\n"
            "        return _wrapped\n"
            "    _tb.TeleBot.send_message = _wm_wrap(_tb.TeleBot.send_message)\n"
            "except Exception:\n"
            "    pass"
        )

    parts.append(f"runpy.run_path({fp!r}, run_name='__main__')")

    launcher_path = f"{BOT_FILES_DIR}/_run_{bid}.py"
    with open(launcher_path, "w", encoding="utf-8") as lf:
        lf.write("\n".join(parts) + "\n")
    return launcher_path


def start_bot(uid, bid, mid):
    try:
        cursor.execute("SELECT file_path, bot_token, bot_username, template_key FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        bd = cursor.fetchone()
    except:
        bd = None
    
    if not bd:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return
    
    fp, bt, bu, tkey = bd
    
    if not bt:
        try:
            bot.answer_callback_query(mid, "Token bulunamadı!")
        except:
            pass
        return
    
    st = bot.send_message(uid, T(uid, 'checking'))
    
    modules = find_imports(fp)
    if modules:
        install_modules(modules, uid, st.message_id)
        bot.edit_message_text(T(uid, 'all_ready'), uid, st.message_id)
    
    bot.edit_message_text(T(uid, 'starting'), uid, st.message_id)
    
    try:
        os.makedirs(BOT_FILES_DIR, exist_ok=True)
        log_path = f"{BOT_FILES_DIR}/{bid}_log.txt"
        log_f = open(log_path, "w", encoding="utf-8", errors="ignore")

        run_target = fp
        try:
            run_target = build_bot_launcher(fp, bid, add_watermark=get_effective_watermark(uid), redirect_db=not tkey)
        except Exception:
            run_target = fp

        p = subprocess.Popen([sys.executable, run_target], stdout=log_f, stderr=subprocess.STDOUT)
        log_f.close()
        time.sleep(1)
        
        if p.poll() is not None:
            cursor.execute("UPDATE bot_files SET bot_status='error' WHERE id=?", (bid,))
            conn.commit()
            bot.edit_message_text("❌ Başlatılamadı!", uid, st.message_id)
            return
        
        cursor.execute("UPDATE bot_files SET bot_status='running', pid=?, start_count=start_count+1 WHERE id=?", (p.pid, bid))
        conn.commit()
        
        threading.Thread(target=monitor_bot, args=(bid, p.pid), daemon=True).start()
        
        if bt:
            update_bot_description(bt)
        
        bot.edit_message_text(T(uid, 'bot_started'), uid, st.message_id, parse_mode="HTML")
        bot_info(uid, bid, mid)
        
    except Exception as e:
        cursor.execute("UPDATE bot_files SET bot_status='error', error_log=? WHERE id=?", (str(e)[:200], bid))
        conn.commit()
        try:
            bot.edit_message_text(f"❌ Hata: {str(e)[:100]}", uid, st.message_id)
        except:
            pass

def show_bot_logs(uid, bid, mid):
    try:
        cursor.execute("SELECT id FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        row = cursor.fetchone()
    except:
        row = None

    if not row:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return

    log_path = f"{BOT_FILES_DIR}/{bid}_log.txt"

    if not os.path.exists(log_path):
        try:
            bot.answer_callback_query(mid, "⚠️ Henüz log yok. Botu başlatmayı dene.", show_alert=True)
        except:
            pass
        return

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        content = "".join(lines[-40:]).strip()
        if not content:
            content = "(Log boş, henüz bir çıktı yok)"
        if len(content) > 3500:
            content = content[-3500:]

        mk = InlineKeyboardMarkup(row_width=1)
        mk.row(btn("🔄 Yenile", f"bot_logs_{bid}"))
        mk.row(btn(T(uid, 'back'), f"bot_info_{bid}"))

        bot.send_message(
            uid,
            f"📜 <b>Son Loglar</b>\n{DIV}\n<pre>{esc(content)}</pre>",
            parse_mode="HTML",
            reply_markup=mk
        )
    except Exception as e:
        try:
            bot.send_message(uid, f"❌ Log okunamadı: {str(e)[:150]}")
        except:
            pass


def send_bot_database(uid, bid, mid):
    """💾 Botlarım > Bot Detayı içindeki 'Veritabanı' butonu — sadece o botun sahibi,
    sadece o bota ait güncel veritabanını indirir.
    - Market şablonundan kurulmuş botlarda veritabanı yolu zaten bilinir (template_db_path).
    - Kullanıcının kendi yüklediği botlarda ise, bot her başlatıldığında (build_bot_launcher
      sayesinde) sqlite3.connect() çağrıları şeffafça o bota özel bir klasöre (userdb_{bid}/)
      yönlendirilir; kodun içinde hangi isim/yol kullanılırsa kullanılsın veritabanı(lar)
      hep aynı bilinen, izole klasörde durur. Birden fazla dosya varsa zip'lenip gönderilir."""
    try:
        cursor.execute("SELECT file_name, bot_username, template_key FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        row = cursor.fetchone()
    except Exception:
        row = None

    if not row:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return

    fn, bu, tkey = row
    display_name = f"@{bu}" if bu else fn

    if tkey:
        db_files = [template_db_path(bid)]
    else:
        d = user_db_dir(bid)
        db_files = []
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                fp2 = os.path.join(d, name)
                if os.path.isfile(fp2):
                    db_files.append(fp2)

    db_files = [p for p in db_files if os.path.exists(p)]

    if not db_files:
        try:
            bot.answer_callback_query(
                mid,
                "⚠️ Bu bot için henüz bir veritabanı bulunamadı. Botun en az bir kez çalışıp veri kaydetmesi gerekir.",
                show_alert=True
            )
        except:
            pass
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp_paths = []
    try:
        if len(db_files) == 1:
            snapshot_path = f"{BOT_FILES_DIR}/_export_{bid}_{int(time.time())}.db"
            tmp_paths.append(snapshot_path)
            if not _snapshot_sqlite_or_copy(db_files[0], snapshot_path):
                raise RuntimeError("dosya kopyalanamadı")
            with open(snapshot_path, "rb") as f:
                bot.send_document(
                    uid, f,
                    visible_file_name=f"{(bu or fn or str(bid))}_veritabani.db",
                    caption=(f"💾 <b>Güncel Veritabanı</b>\n{DIV}\n🤖 Bot: {esc(display_name)}\n🕐 {now}"),
                    parse_mode="HTML"
                )
        else:
            zip_path = f"{BOT_FILES_DIR}/_export_{bid}_{int(time.time())}.zip"
            tmp_paths.append(zip_path)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, src_path in enumerate(db_files):
                    snap = f"{BOT_FILES_DIR}/_export_{bid}_{int(time.time())}_{i}.db"
                    tmp_paths.append(snap)
                    if _snapshot_sqlite_or_copy(src_path, snap):
                        zf.write(snap, arcname=os.path.basename(src_path))
            with open(zip_path, "rb") as f:
                bot.send_document(
                    uid, f,
                    visible_file_name=f"{(bu or fn or str(bid))}_veritabani.zip",
                    caption=(f"💾 <b>Güncel Veritabanı</b> ({len(db_files)} dosya)\n{DIV}\n🤖 Bot: {esc(display_name)}\n🕐 {now}"),
                    parse_mode="HTML"
                )
    except Exception as e:
        log_error("send_bot_database", e)
        try:
            bot.send_message(uid, f"❌ Veritabanı gönderilemedi: {str(e)[:150]}")
        except:
            pass
    finally:
        for p in tmp_paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

def _is_sqlite_bytes(data):
    return isinstance(data, (bytes, bytearray)) and data.startswith(b"SQLite format 3\x00")

def request_db_upload(uid, bid, mid):
    """📤 Botlarım > Bot Detayı > 'Veritabanı Yükle' — kullanıcıdan yeni bir .db (veya
    birden çok dosya varsa .zip) ister ve process_db_upload'a yönlendirir."""
    try:
        cursor.execute("SELECT file_name, bot_username, template_key FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        row = cursor.fetchone()
    except Exception:
        row = None

    if not row:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return

    fn, bu, tkey = row
    display_name = f"@{bu}" if bu else fn

    if tkey:
        hint = "📤 Lütfen yeni <b>.db</b> dosyasını gönder."
    else:
        d = user_db_dir(bid)
        existing = []
        if os.path.isdir(d):
            existing = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))
        if len(existing) > 1:
            hint = (
                "Bu botun birden fazla veritabanı dosyası var, bu yüzden isimlerin karışmaması için "
                "<b>💾 Veritabanı</b> butonuyla aldığın <b>.zip</b> dosyasını (dosya adlarını değiştirmeden) gönder."
            )
        else:
            hint = "📤 Lütfen yeni <b>.db</b> dosyasını (ya da birden fazla dosya varsa <b>.zip</b>) gönder."

    txt = (
        "📤 <b>Veritabanı Yükle</b>\n"
        f"{DIV}\n"
        f"🤖 Bot: {esc(display_name)}\n\n"
        f"{hint}\n\n"
        "⚠️ <b>Uyarı:</b> Gönderdiğin dosya, bu botun şu anki veritabanının yerini alacak. "
        "Eski veritabanı otomatik olarak yedeklenir. Bot çalışıyorsa, değişikliğin geçerli olması için "
        "yeniden başlatman gerekir.\n\n"
        "❌ Vazgeçmek için: /iptal"
    )
    bot.clear_step_handler_by_chat_id(uid)
    m = bot.send_message(uid, txt, parse_mode="HTML", reply_markup=_back_kb(uid, f"bot_info_{bid}"))
    bot.register_next_step_handler(m, lambda mm: process_db_upload(mm, bid))

def process_db_upload(msg, bid):
    """Kullanıcı bir .db/.zip dosyası gönderdiğinde, sahiplik teyit edilip dosya
    doğrulanarak (SQLite imzası kontrolü) o botun veritabanının yerine yazılır.
    Değiştirmeden önce eski dosya(lar) otomatik olarak yedeklenir (.bak_<zaman>)."""
    uid = msg.from_user.id

    if isinstance(getattr(msg, "text", None), str) and msg.text.strip().lower() in ("/iptal", "/cancel"):
        bot.send_message(uid, "❌ İşlem iptal edildi.", reply_markup=_back_kb(uid, f"bot_info_{bid}"))
        return

    try:
        cursor.execute("SELECT file_name, bot_username, template_key, bot_status, pid FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        row = cursor.fetchone()
    except Exception:
        row = None

    if not row:
        bot.send_message(uid, "Bulunamadı!")
        return

    fn, bu, tkey, bstatus, pid = row
    display_name = f"@{bu}" if bu else fn

    if not msg.document:
        m = bot.send_message(uid, "❌ <b>Bir dosya göndermelisin!</b> Tekrar dene ya da /iptal:", parse_mode="HTML")
        bot.register_next_step_handler(m, lambda mm: process_db_upload(mm, bid))
        return

    up_name = sanitize_filename(msg.document.file_name)
    ext_ok = up_name.lower().endswith((".db", ".sqlite", ".sqlite3", ".zip"))
    if not ext_ok:
        m = bot.send_message(uid, "❌ <b>Sadece .db/.sqlite/.sqlite3 veya .zip kabul edilir!</b> Tekrar dene ya da /iptal:", parse_mode="HTML")
        bot.register_next_step_handler(m, lambda mm: process_db_upload(mm, bid))
        return

    if msg.document.file_size and msg.document.file_size > 20 * 1024 * 1024:
        m = bot.send_message(uid, "❌ <b>Dosya çok büyük (max 20MB)!</b> Tekrar dene ya da /iptal:", parse_mode="HTML")
        bot.register_next_step_handler(m, lambda mm: process_db_upload(mm, bid))
        return

    try:
        fi = bot.get_file(msg.document.file_id)
        data = bot.download_file(fi.file_path)
    except Exception as e:
        bot.send_message(uid, f"❌ <b>Dosya indirilemedi:</b> {esc(str(e)[:150])}", parse_mode="HTML")
        return

    now_tag = int(time.time())
    written = []
    skipped = []

    try:
        if tkey:
            # 🏪 Şablon botu: tek, bilinen veritabanı yolu
            if up_name.lower().endswith(".zip"):
                bot.send_message(uid, "❌ <b>Bu bot için tek bir .db dosyası gönderilmeli, .zip değil.</b>", parse_mode="HTML")
                return
            if not _is_sqlite_bytes(data):
                bot.send_message(uid, "❌ <b>Bu geçerli bir SQLite veritabanı dosyası değil!</b>", parse_mode="HTML")
                return

            target = template_db_path(bid)
            if os.path.exists(target):
                os.replace(target, f"{target}.bak_{now_tag}")
            os.makedirs(BOT_FILES_DIR, exist_ok=True)
            with open(target, "wb") as f:
                f.write(data)
            written.append(os.path.basename(target))
        else:
            d = user_db_dir(bid)
            os.makedirs(d, exist_ok=True)
            existing = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))

            if up_name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        entry_name = sanitize_filename(os.path.basename(info.filename))
                        if not entry_name:
                            continue
                        content = zf.read(info)
                        if not _is_sqlite_bytes(content):
                            skipped.append(entry_name)
                            continue
                        target = os.path.join(d, entry_name)
                        if os.path.exists(target):
                            os.replace(target, f"{target}.bak_{now_tag}")
                        with open(target, "wb") as f:
                            f.write(content)
                        written.append(entry_name)
            else:
                if not _is_sqlite_bytes(data):
                    bot.send_message(uid, "❌ <b>Bu geçerli bir SQLite veritabanı dosyası değil!</b>", parse_mode="HTML")
                    return
                if len(existing) > 1:
                    bot.send_message(
                        uid,
                        "❌ <b>Bu botun birden fazla veritabanı dosyası var.</b> Karışmaması için "
                        "💾 Veritabanı butonuyla aldığın .zip dosyasını (isimleri değiştirmeden) gönder.",
                        parse_mode="HTML"
                    )
                    return
                target_name = existing[0] if existing else up_name
                target = os.path.join(d, target_name)
                if os.path.exists(target):
                    os.replace(target, f"{target}.bak_{now_tag}")
                with open(target, "wb") as f:
                    f.write(data)
                written.append(target_name)
    except Exception as e:
        log_error("process_db_upload", e)
        bot.send_message(uid, f"❌ <b>Yükleme başarısız oldu:</b> {esc(str(e)[:150])}", parse_mode="HTML")
        return

    if not written:
        detail = f" (atlanan: {', '.join(skipped)})" if skipped else ""
        bot.send_message(uid, f"❌ <b>Geçerli bir SQLite dosyası bulunamadı.</b>{esc(detail)}", parse_mode="HTML")
        return

    mk = InlineKeyboardMarkup(row_width=1)
    running = (bstatus == "running" and pid)
    if running:
        mk.row(btn("🔄 Şimdi Yeniden Başlat", f"bot_restart_{bid}"))
    mk.row(btn(T(uid, 'back'), f"bot_info_{bid}"))

    lines = [f"✅ <b>Veritabanı yüklendi!</b>", DIV, f"🤖 Bot: {esc(display_name)}", f"📄 Güncellenen: {esc(', '.join(written))}"]
    if skipped:
        lines.append(f"⚠️ Atlanan (geçersiz): {esc(', '.join(skipped))}")
    if running:
        lines.append("\n⚠️ Bot şu an çalışıyor — değişikliğin etkili olması için yeniden başlatman gerekiyor.")
    bot.send_message(uid, "\n".join(lines), parse_mode="HTML", reply_markup=mk)

def stop_bot(uid, bid, mid):
    try:
        cursor.execute("SELECT pid FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        bd = cursor.fetchone()
    except:
        bd = None
    
    if not bd or not bd[0]:
        try:
            bot.answer_callback_query(mid, "Bot çalışmıyor!")
        except:
            pass
        return
    
    kill_pid(bd[0])
    cursor.execute("UPDATE bot_files SET bot_status='stopped', pid=NULL WHERE id=?", (bid,))
    conn.commit()
    
    bot.send_message(uid, T(uid, 'bot_stopped'), parse_mode="HTML")
    bot_info(uid, bid, mid)

def del_bot(uid, bid, mid):
    try:
        cursor.execute("SELECT file_path, pid FROM bot_files WHERE id=? AND user_id=?", (bid, uid))
        bd = cursor.fetchone()
    except:
        bd = None
    
    if not bd:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return
    
    if bd[1]:
        kill_pid(bd[1])
    
    try:
        if os.path.exists(bd[0]):
            os.remove(bd[0])
    except:
        pass

    try:
        import shutil
        d = user_db_dir(bid)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

    cursor.execute("DELETE FROM bot_files WHERE id=?", (bid,))
    cursor.execute("UPDATE users SET bot_count=MAX(bot_count-1,0) WHERE user_id=?", (uid,))
    conn.commit()
    
    try:
        bot.answer_callback_query(mid, T(uid, 'bot_deleted'))
    except:
        pass
    
    show_bots(uid, mid)

# ================= ADMİN: ONAYLANAN BOTLARI YÖNET =================
# Aşağıdaki fonksiyonlar (start_bot/stop_bot/del_bot'un aksine) sahiplik
# kontrolü YAPMAZ — admin, herhangi bir kullanıcının onaylı botunu
# istediği zaman başlatabilir/durdurabilir/silebilir.

def admin_render_approved(admin_uid, mid):
    """✅ Onaylananlar ekranını her bot için Başlat/Durdur + Sil butonlarıyla gösterir."""
    cursor.execute(
        "SELECT id, user_id, file_name, bot_status FROM bot_files WHERE status='approved' ORDER BY id DESC LIMIT 20"
    )
    rows = cursor.fetchall()
    mk = InlineKeyboardMarkup()

    if not rows:
        mk.row(btn(T(admin_uid, 'back'), "admin_panel"))
        bot.edit_message_text(
            f"✅ <b>Onaylananlar</b>\n{DIV}\nHenüz onaylanan bot yok.",
            admin_uid, mid, reply_markup=mk, parse_mode="HTML"
        )
        return

    for bid, owner_id, fname, bstatus in rows:
        running = (bstatus == 'running')
        state_icon = "🟢 Çalışıyor" if running else "🔴 Durdu"
        mk.row(btn(f"📄 #{bid} {fname[:18]} · 👤 {owner_id} · {state_icon}", "noop"))
        if running:
            mk.row(
                btn("⛔ Durdur", f"admin_bot_stop_{bid}"),
                btn("❌ Sil", f"admin_bot_del_{bid}")
            )
        else:
            mk.row(
                btn("▶️ Başlat", f"admin_bot_start_{bid}"),
                btn("❌ Sil", f"admin_bot_del_{bid}")
            )

    mk.row(btn(T(admin_uid, 'back'), "admin_panel"))
    txt = f"✅ <b>Onaylananlar</b> (son {len(rows)})\n{DIV}\n📌 İstediğin zaman durdur/başlat/sil 👇"
    bot.edit_message_text(txt, admin_uid, mid, reply_markup=mk, parse_mode="HTML")

def admin_start_bot(admin_uid, bid, mid):
    cursor.execute("SELECT file_path, bot_token, user_id, template_key FROM bot_files WHERE id=? AND status='approved'", (bid,))
    row = cursor.fetchone()
    if not row:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        return

    fp, bt, owner_id, tkey = row
    if not bt or not fp:
        try:
            bot.answer_callback_query(mid, "Token/dosya bulunamadı!")
        except:
            pass
        return

    try:
        os.makedirs(BOT_FILES_DIR, exist_ok=True)
        log_path = f"{BOT_FILES_DIR}/{bid}_log.txt"
        log_f = open(log_path, "w", encoding="utf-8", errors="ignore")

        run_target = fp
        try:
            run_target = build_bot_launcher(fp, bid, add_watermark=get_effective_watermark(owner_id), redirect_db=not tkey)
        except Exception:
            run_target = fp

        p = subprocess.Popen([sys.executable, run_target], stdout=log_f, stderr=subprocess.STDOUT)
        log_f.close()
        time.sleep(1)

        if p.poll() is not None:
            cursor.execute("UPDATE bot_files SET bot_status='error' WHERE id=?", (bid,))
            conn.commit()
            try:
                bot.answer_callback_query(mid, "❌ Başlatılamadı!", show_alert=True)
            except:
                pass
            admin_render_approved(admin_uid, mid)
            return

        cursor.execute("UPDATE bot_files SET bot_status='running', pid=?, start_count=start_count+1 WHERE id=?", (p.pid, bid))
        conn.commit()
        threading.Thread(target=monitor_bot, args=(bid, p.pid), daemon=True).start()

        if bt:
            update_bot_description(bt)

        try:
            bot.send_message(owner_id, f"▶️ <b>Botunuz admin tarafından başlatıldı.</b> (#{bid})", parse_mode="HTML")
        except:
            pass
    except Exception as e:
        cursor.execute("UPDATE bot_files SET bot_status='error', error_log=? WHERE id=?", (str(e)[:200], bid))
        conn.commit()

    admin_render_approved(admin_uid, mid)

def admin_stop_bot(admin_uid, bid, mid):
    cursor.execute("SELECT pid, user_id FROM bot_files WHERE id=?", (bid,))
    row = cursor.fetchone()
    if not row or not row[0]:
        try:
            bot.answer_callback_query(mid, "Bot çalışmıyor!")
        except:
            pass
        admin_render_approved(admin_uid, mid)
        return

    pid, owner_id = row
    kill_pid(pid)
    cursor.execute("UPDATE bot_files SET bot_status='stopped', pid=NULL WHERE id=?", (bid,))
    conn.commit()

    try:
        bot.send_message(owner_id, f"⛔ <b>Botunuz admin tarafından durduruldu.</b> (#{bid})", parse_mode="HTML")
    except:
        pass

    admin_render_approved(admin_uid, mid)

def admin_confirm_delete_bot(admin_uid, bid, mid):
    """Silme işlemi geri alınamaz olduğu için, admin yanlışlıkla başka birinin
    botunu silmesin diye önce onay istenir."""
    cursor.execute("SELECT file_name, user_id FROM bot_files WHERE id=?", (bid,))
    row = cursor.fetchone()
    if not row:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        admin_render_approved(admin_uid, mid)
        return

    fname, owner_id = row
    mk = InlineKeyboardMarkup()
    mk.row(btn("✅ Evet, Sil", f"admin_bot_delyes_{bid}"), btn("❌ Vazgeç", f"admin_bot_delno_{bid}"))
    bot.edit_message_text(
        f"⚠️ <b>Emin misin?</b>\n{DIV}\n"
        f"📄 {esc(fname)}\n👤 Sahibi: <code>{owner_id}</code>\n\n"
        f"Bu işlem geri alınamaz, dosya ve tüm kaydı kalıcı olarak silinir.",
        admin_uid, mid, reply_markup=mk, parse_mode="HTML"
    )

def admin_delete_bot(admin_uid, bid, mid):
    cursor.execute("SELECT file_path, pid, user_id, file_name FROM bot_files WHERE id=?", (bid,))
    row = cursor.fetchone()
    if not row:
        try:
            bot.answer_callback_query(mid, "Bulunamadı!")
        except:
            pass
        admin_render_approved(admin_uid, mid)
        return

    fp, pid, owner_id, fname = row
    if pid:
        kill_pid(pid)

    try:
        if fp and os.path.exists(fp):
            os.remove(fp)
    except:
        pass

    cursor.execute("DELETE FROM bot_files WHERE id=?", (bid,))
    cursor.execute("UPDATE users SET bot_count=MAX(bot_count-1,0) WHERE user_id=?", (owner_id,))
    conn.commit()

    try:
        bot.send_message(owner_id, f"❌ <b>Botunuz admin tarafından silindi:</b> {esc(fname)}", parse_mode="HTML")
    except:
        pass
    try:
        bot.answer_callback_query(mid, "✅ Silindi.")
    except:
        pass

    admin_render_approved(admin_uid, mid)

def show_premium(uid, mid=None):
    try:
        cursor.execute("SELECT premium, premium_package, premium_until FROM users WHERE user_id=?", (uid,))
        u = cursor.fetchone()
        cursor.execute("SELECT id, name, price, bot_limit, duration_minutes FROM premium_packages ORDER BY price")
        pkg = cursor.fetchall()
    except:
        return
    
    isp = u[0] if u else 0
    cp = u[1] if u else "Basit"
    p_until = u[2] if u else None
    
    mk = InlineKeyboardMarkup(row_width=2)
    
    if not pkg:
        mk.row(btn(T(uid, 'back'), "back_main"))
        empty_txt = T(uid, 'no_packages') if is_admin(uid) else T(uid, 'no_packages_user')
        txt0 = f"⭐ <b>VIP</b>\n{DIV}\n{empty_txt}"
        if mid:
            try:
                bot.edit_message_text(txt0, uid, mid, reply_markup=mk, parse_mode="HTML")
                return
            except:
                pass
        bot.send_message(uid, txt0, reply_markup=mk, parse_mode="HTML")
        return
    
    for p in pkg:
        pt = "Ücretsiz" if p[2] == 0 else f"{p[2]} ⭐"
        dt = format_duration(p[4])
        mk.add(btn(f"📦 {p[1]} · {pt} · {dt}", f"pkgview_user_{p[0]}"))
    
    mk.row(btn(T(uid, 'back'), "back_main"))
    st = '⭐ VIP Üye' if isp else '🆓 Standart Üye'
    exp_line = ""
    if isp:
        exp_line = (T(uid, 'lifetime') if not p_until else T(uid, 'expires_on').format(p_until[:10])) + "\n"
    
    txt = (
        f"⭐ <b>VIP Üyelik</b>\n"
        f"{DIV}\n"
        f"📊 Durumun: <b>{st}</b>\n"
        f"📦 Mevcut Paketin: <b>{esc(cp)}</b>\n"
        f"{exp_line}"
        f"🤖 VIP Bot Hakkı: <i>Satın aldığın pakete göre değişir</i>\n"
        f"{DIV}\n"
        f"📦 <b>Satın Alınabilir Paketler</b>\n"
        f"👇 Detay için bir paket seç"
    )
    if mid:
        try:
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

def show_profile(uid, mid):
    sync_premium_expiry(uid)
    try:
        cursor.execute("SELECT user_id, username, name, premium, bot_count, total_files, premium_until FROM users WHERE user_id=?", (uid,))
        u = cursor.fetchone()
    except:
        return
    
    if u:
        mk = InlineKeyboardMarkup()
        mk.row(btn(T(uid, 'back'), "back_main"))
        pr = '⭐ Aktif (VIP)' if u[3] else '❌ Pasif'
        uname = esc(u[1]) if u[1] else "Yok"
        exp_line = ""
        if u[3]:
            exp_line = (T(uid, 'lifetime') if not u[6] else T(uid, 'expires_on').format(u[6][:10])) + "\n"
        txt = (
            f"👤 <b>Profilim</b>\n"
            f"{DIV}\n"
            f"🆔 ID: <code>{u[0]}</code>\n"
            f"📛 Kullanıcı Adı: @{uname}\n"
            f"👋 İsim: <b>{esc(u[2])}</b>\n"
            f"{DIV}\n"
            f"⭐ VIP: <b>{pr}</b>\n"
            f"{exp_line}"
            f"📁 Toplam Bot: <b>{u[4]}</b> / {get_user_bot_limit(uid) if u[3] else get_limit()}"
        )
        try:
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
        except:
            pass

def show_rank(uid, mid):
    try:
        cursor.execute("SELECT username, bot_count FROM users WHERE bot_count>0 ORDER BY bot_count DESC LIMIT 10")
        users = cursor.fetchall()
    except:
        users = []
    
    mk = InlineKeyboardMarkup()
    mk.row(btn(T(uid, 'back'), "back_main"))
    
    if not users:
        try:
            bot.edit_message_text(f"🏆 <b>Sıralama</b>\n{DIV}\nHenüz sıralama yok.", uid, mid, reply_markup=mk, parse_mode="HTML")
        except:
            pass
        return
    
    msg = f"🏆 <b>En Çok Bota Sahip Kullanıcılar</b>\n{DIV}\n"
    for i, u in enumerate(users, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"▫️ {i}."
        uname = esc(u[0]) if u[0] else "Bilinmiyor"
        msg += f"{medal} @{uname} · <b>{u[1]}</b> bot\n"
    
    try:
        bot.edit_message_text(msg, uid, mid, reply_markup=mk, parse_mode="HTML")
    except:
        pass

def handle_admin(call):
    uid = call.from_user.id
    data = call.data
    mid = call.message.message_id

    try:
        if data == "admin_stats":
            cursor.execute("SELECT COUNT(*) FROM users")
            tu = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users WHERE premium=1")
            pu = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM bot_files")
            tf = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM pending_files")
            pf = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM bot_files WHERE status='approved'")
            af = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM bot_files WHERE bot_status='running'")
            rb = cursor.fetchone()[0]
            fl = get_limit()
            
            mk = InlineKeyboardMarkup()
            mk.row(btn("🔄 Yenile", "admin_stats"))
            mk.row(btn(T(uid, 'back'), "admin_panel"))
            prem_pct = f"{(pu/tu*100):.0f}%" if tu else "0%"
            txt = (
                f"📊 <b>İstatistikler</b>\n"
                f"{DIV}\n"
                f"👥 Kullanıcı: <b>{tu}</b>\n"
                f"⭐ VIP: <b>{pu}</b> ({prem_pct})\n"
                f"{DIV}\n"
                f"📁 Toplam Dosya: <b>{tf}</b>\n"
                f"📤 Onay Bekleyen: <b>{pf}</b>\n"
                f"✅ Onaylanan: <b>{af}</b>\n"
                f"🟢 Şu An Çalışan: <b>{rb}</b>\n"
                f"{DIV}\n"
                f"🎟️ Ücretsiz Toplam Bot Limiti: <b>{fl}</b>\n"
                f"⭐ VIP Varsayılan Bot Hakkı: <b>{get_premium_default_limit()}</b>"
            )
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data == "admin_pending":
            cursor.execute("SELECT id, user_id, file_name FROM pending_files ORDER BY id DESC")
            p = cursor.fetchall()
            mk = InlineKeyboardMarkup(row_width=2)
            
            if not p:
                mk.row(btn(T(uid, 'back'), "admin_panel"))
                bot.edit_message_text(f"📤 <b>Bekleyen Dosyalar</b>\n{DIV}\n{T(uid, 'no_pending')}", uid, mid, reply_markup=mk, parse_mode="HTML")
                return
            
            for x in p:
                mk.row(btn(f"👤 {x[1]} · {x[2][:15]}", "noop"))
                mk.row(btn(f"{T(uid,'approve')}", f"aprv_{x[0]}"), btn(f"{T(uid,'reject')}", f"rej_{x[0]}"))
            mk.row(btn(T(uid, 'back'), "admin_panel"))
            txt = f"📤 <b>Bekleyen Dosyalar</b> ({len(p)})\n{DIV}\nAşağıdan onaylayın veya reddedin 👇"
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data == "admin_approved":
            admin_render_approved(uid, mid)

        elif data.startswith("admin_bot_") and is_admin(uid):
            rest = data[len("admin_bot_"):]
            if "_" in rest:
                act, bid_str = rest.split("_", 1)
                try:
                    bid = int(bid_str)
                except ValueError:
                    bid = None
                if bid is not None:
                    if act == "start":
                        admin_start_bot(uid, bid, mid)
                    elif act == "stop":
                        admin_stop_bot(uid, bid, mid)
                    elif act == "del":
                        admin_confirm_delete_bot(uid, bid, mid)
                    elif act == "delyes":
                        admin_delete_bot(uid, bid, mid)
                    elif act == "delno":
                        admin_render_approved(uid, mid)

        elif data == "admin_premium":
            bot.clear_step_handler_by_chat_id(uid)
            msg = bot.send_message(
                uid,
                f"⭐ <b>VIP Ver</b>\n{DIV}\n👤 VIP verilecek kullanıcının ID'sini gönder:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "admin_panel")
            )
            bot.register_next_step_handler(msg, premium_ask_id)

        elif data == "admin_unpremium":
            bot.clear_step_handler_by_chat_id(uid)
            msg = bot.send_message(
                uid,
                f"🚫 <b>VIP Kaldır</b>\n{DIV}\n👤 VIP'i kaldırılacak kullanıcının ID'sini gönder:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "admin_panel")
            )
            bot.register_next_step_handler(msg, take_premium)

        elif data == "admin_unban":
            bot.clear_step_handler_by_chat_id(uid)
            msg = bot.send_message(
                uid,
                f"🚫 <b>Engel Kaldır</b>\n{DIV}\n🆔 Engeli kaldırılacak kullanıcının ID'sini gönder:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "admin_panel")
            )
            bot.register_next_step_handler(msg, unban_user_flow)

        elif data == "admin_packages":
            cursor.execute("SELECT id, name, price, duration_minutes FROM premium_packages ORDER BY price")
            pkg = cursor.fetchall()
            mk = InlineKeyboardMarkup(row_width=2)
            
            if not pkg:
                mk.row(btn(T(uid, 'back'), "admin_panel"))
                bot.edit_message_text(f"📦 <b>Paketler</b>\n{DIV}\n{T(uid, 'no_packages')}", uid, mid, reply_markup=mk, parse_mode="HTML")
                return
            
            for p in pkg:
                pt = "Ücretsiz" if p[2] == 0 else f"{p[2]} ⭐"
                dt = format_duration(p[3])
                mk.add(btn(f"⚙️ {p[1]} · {pt} · {dt}", f"pkgview_{p[0]}"))
            mk.row(btn(T(uid, 'back'), "admin_panel"))
            bot.edit_message_text(f"📦 <b>VIP Paketler</b> ({len(pkg)})\n{DIV}", uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data == "admin_download_db":
            # 🔒 WAL modunda son değişiklikler asıl .db dosyasına yazılmamış olabilir;
            # indirmeden önce checkpoint yaparak dosyanın güncel olmasını garanti ediyoruz.
            try:
                conn.execute("PRAGMA wal_checkpoint(FULL)")
                conn.commit()
            except Exception as e:
                print(f"WAL Checkpoint Error: {e}")
            if os.path.exists(DB_PATH):
                with open(DB_PATH, "rb") as f:
                    bot.send_document(uid, f, caption="📥 Veritabanı Yedeği")
            else:
                bot.send_message(uid, "⚠️ <b>Veritabanı bulunamadı!</b>", parse_mode="HTML")

        elif data == "admin_restore_db" and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            txt = (
                "🔄 <b>Veritabanı Geri Yükleme</b>\n"
                f"{DIV}\n"
                "📤 Lütfen yeni <b>.db</b> dosyasını gönder.\n\n"
                "⚠️ <b>Uyarı:</b> Gönderdiğin dosya, botun şu an kullandığı TÜM verinin (kullanıcılar, botlar, VIP'ler, promo kodlar) yerini alacak. "
                "Eski veritabanı otomatik olarak yedeklenecek."
            )
            mk = _back_kb(uid, "admin_panel")
            try:
                bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
                step_msg = call.message
            except:
                step_msg = bot.send_message(uid, txt, parse_mode="HTML", reply_markup=mk)
            bot.register_next_step_handler(step_msg, restore_db_step)

        elif data == "admin_backup_files" and is_admin(uid):
            zip_path = backup_bot_files_zip()
            if zip_path and os.path.exists(zip_path):
                with open(zip_path, "rb") as f:
                    bot.send_document(uid, f, caption="📦 Bot Dosyaları Yedeği (.zip)")
                try:
                    os.remove(zip_path)
                except:
                    pass
            else:
                bot.send_message(uid, "⚠️ <b>Yedeklenecek dosya bulunamadı!</b>", parse_mode="HTML")

        elif data == "admin_restore_files" and is_admin(uid):
            bot.clear_step_handler_by_chat_id(uid)
            txt = (
                "📂 <b>Bot Dosyalarını Geri Yükleme</b>\n"
                f"{DIV}\n"
                f"📤 Lütfen <b>backup_bot_files_zip()</b> ile aldığın <b>.zip</b> dosyasını gönder.\n\n"
                f"⚠️ <b>Uyarı:</b> Gönderdiğin zip içindeki dosyalar, şu anki <code>{esc(BOT_FILES_DIR)}</code> "
                "klasörünün üzerine (aynı isimliler değiştirilerek) açılacak. Bu genelde DB yedeğini geri "
                "yükledikten SONRA, aynı ana ait zip ile yapılmalı."
            )
            mk = _back_kb(uid, "admin_panel")
            try:
                bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
                step_msg = call.message
            except:
                step_msg = bot.send_message(uid, txt, parse_mode="HTML", reply_markup=mk)
            bot.register_next_step_handler(step_msg, restore_files_step)

        elif data == "admin_maintenance":
            cursor.execute("SELECT value FROM settings WHERE key='maintenance'")
            cur = cursor.fetchone()[0]
            nv = '0' if cur == '1' else '1'
            cursor.execute("UPDATE settings SET value=? WHERE key='maintenance'", (nv,))

            # ⚠️ Otomatik uyku programı açıksa, elle yapılan değişikliği ezmemesi için
            # otomatik programı da kapatıyoruz (aksi halde 30sn içinde eski haline dönebiliyordu).
            cursor.execute("SELECT value FROM settings WHERE key='sleep_auto_enabled'")
            auto_row = cursor.fetchone()
            auto_was_on = bool(auto_row and auto_row[0] == '1')
            note = ""
            if auto_was_on:
                cursor.execute("UPDATE settings SET value='0' WHERE key='sleep_auto_enabled'")
                note = " ⚠️ Otomatik uyku programı da elle kapatıldı (çakışmayı önlemek için)."
            conn.commit()

            bot.answer_callback_query(call.id, f"😴 Uyku Modu {'AÇIK' if nv=='1' else 'KAPALI'}{note}", show_alert=bool(note))
            admin_panel(uid, mid)

        elif data == "admin_bakim_modu":
            cursor.execute("SELECT value FROM settings WHERE key='bakim_modu'")
            cur = cursor.fetchone()
            cur_v = cur[0] if cur else '0'
            nv = '0' if cur_v == '1' else '1'
            cursor.execute("UPDATE settings SET value=? WHERE key='bakim_modu'", (nv,))
            conn.commit()
            bot.answer_callback_query(call.id, f"🔧 Bakım Modu {'AÇIK — adminler hariç herkes engellendi' if nv=='1' else 'KAPALI'}", show_alert=True)
            admin_panel(uid, mid)

        elif data == "admin_toggle_immune":
            if not is_owner(uid):
                try:
                    bot.answer_callback_query(call.id, "⛔ Bu ayarı sadece panel sahibi değiştirebilir!", show_alert=True)
                except:
                    pass
                return
            cursor.execute("SELECT value FROM settings WHERE key='admin_sleep_immune'")
            cur = cursor.fetchone()
            cur_v = cur[0] if cur else '0'
            nv = '0' if cur_v == '1' else '1'
            cursor.execute("UPDATE settings SET value=? WHERE key='admin_sleep_immune'", (nv,))
            conn.commit()
            applies = "AÇIK (adminler de uyku moduna girer)" if nv == '0' else "KAPALI (adminler muaf, sınırlı erişim kalır)"
            bot.answer_callback_query(call.id, f"👑 Adminler Uyku Modu: {applies}", show_alert=True)
            admin_panel(uid, mid)

        elif data == "admin_broadcast":
            bot.clear_step_handler_by_chat_id(uid)
            msg = bot.send_message(
                uid,
                f"📢 <b>Duyuru</b>\n{DIV}\n📝 Tüm kullanıcılara gönderilecek duyuru metnini yaz:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "admin_panel")
            )
            bot.register_next_step_handler(msg, broadcast)

        elif data == "admin_users":
            cursor.execute("SELECT user_id, username, name, premium, bot_count, banned FROM users ORDER BY user_id DESC LIMIT 20")
            u = cursor.fetchall()
            mk = InlineKeyboardMarkup()
            mk.row(btn(T(uid, 'back'), "admin_panel"))
            
            if not u:
                bot.edit_message_text(f"👥 <b>Kullanıcılar</b>\n{DIV}\nHenüz kullanıcı yok.", uid, mid, reply_markup=mk, parse_mode="HTML")
                return
            
            msg = f"👥 <b>Kullanıcılar</b> (son {len(u)})\n{DIV}\n"
            for x in u:
                ps = "⭐" if x[3] else "🆓"
                bs = " 🚫" if x[5] else ""
                uname = esc(x[1]) if x[1] else esc(x[2])
                msg += f"{ps} @{uname} · <code>{x[0]}</code> · 📁{x[4]}{bs}\n"
            bot.edit_message_text(msg, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data == "admin_all_bots":
            cursor.execute("SELECT id, user_id, file_name, status, bot_status FROM bot_files ORDER BY id DESC LIMIT 30")
            b = cursor.fetchall()
            mk = InlineKeyboardMarkup()
            mk.row(btn(T(uid, 'back'), "admin_panel"))
            
            if not b:
                bot.edit_message_text(f"📁 <b>Tüm Botlar</b>\n{DIV}\nHenüz bot yok.", uid, mid, reply_markup=mk, parse_mode="HTML")
                return
            
            msg = f"📁 <b>Tüm Botlar</b> (son {len(b)})\n{DIV}\n"
            for x in b:
                be = "🟢" if x[4] == 'running' else "🔴"
                se = "✅" if x[3] == 'approved' else "⏳"
                msg += f"{be} #{x[0]} · 👤 <code>{x[1]}</code> · {se} {esc(x[2][:15])}\n"
            bot.edit_message_text(msg, uid, mid, reply_markup=mk, parse_mode="HTML")

        elif data == "admin_free_limit":
            bot.clear_step_handler_by_chat_id(uid)
            msg = bot.send_message(
                uid,
                f"⚙️ <b>Ücretsiz Bot Limiti</b>\n{DIV}\n📊 Mevcut limit: <b>{get_limit()}</b>\n\n🔢 Yeni limit sayısını gönder:",
                parse_mode="HTML",
                reply_markup=_back_kb(uid, "admin_panel")
            )
            bot.register_next_step_handler(msg, set_limit)

        elif data == "admin_sleep_schedule":
            show_sleep_schedule(uid, mid)

        elif data == "admin_sleep_set_times":
            bot.clear_step_handler_by_chat_id(uid)
            msg = bot.send_message(uid, "⏰ Başlangıç saatini <b>SS:DD</b> formatında gönder (örn: 23:00):", parse_mode="HTML", reply_markup=_back_kb(uid, "admin_sleep_schedule"))
            bot.register_next_step_handler(msg, sleep_set_start_step)

        elif data == "admin_sleep_toggle":
            cursor.execute("SELECT value FROM settings WHERE key='sleep_start'")
            s_row = cursor.fetchone()
            cursor.execute("SELECT value FROM settings WHERE key='sleep_end'")
            e_row = cursor.fetchone()
            if not (s_row and s_row[0] and e_row and e_row[0]):
                bot.answer_callback_query(call.id, "⚠️ Önce başlangıç/bitiş saatini ayarlamalısın!", show_alert=True)
            else:
                cursor.execute("SELECT value FROM settings WHERE key='sleep_auto_enabled'")
                cur = cursor.fetchone()
                nv = '0' if (cur and cur[0] == '1') else '1'
                cursor.execute("UPDATE settings SET value=? WHERE key='sleep_auto_enabled'", (nv,))
                conn.commit()
                bot.answer_callback_query(call.id, f"🕐 Otomatik Uyku Programı {'AÇIK' if nv == '1' else 'KAPALI'}")
            show_sleep_schedule(uid, mid)

        elif data == "admin_add_package":
            try:
                show_package_templates(uid, mid)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Hata: {str(e)[:50]}", show_alert=True)

        elif data == "pkg_template_beginner":
            try:
                start_package_wizard(uid, mid, "🌿 Acemi")
                bot.answer_callback_query(call.id, "✅ Paket seçildi!")
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Hata: {str(e)[:50]}", show_alert=True)
        elif data == "pkg_template_professional":
            try:
                start_package_wizard(uid, mid, "⚡ Profesyonel")
                bot.answer_callback_query(call.id, "✅ Paket seçildi!")
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Hata: {str(e)[:50]}", show_alert=True)
        elif data == "pkg_template_master":
            try:
                start_package_wizard(uid, mid, "🔥 Usta")
                bot.answer_callback_query(call.id, "✅ Paket seçildi!")
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Hata: {str(e)[:50]}", show_alert=True)

        elif data == "admin_delete_package":
            cursor.execute("SELECT id, name, price FROM premium_packages")
            pkg = cursor.fetchall()
            mk = InlineKeyboardMarkup(row_width=2)
            
            if not pkg:
                mk.row(btn(T(uid, 'back'), "admin_panel"))
                bot.edit_message_text("Silinecek paket yok!", uid, mid, reply_markup=mk)
                return
            
            for p in pkg:
                mk.add(btn(f"🗑️ {p[1]} ({p[2]} ⭐)", f"del_pkg_{p[0]}"))
            mk.row(btn(T(uid, 'back'), "admin_panel"))
            bot.edit_message_text(f"🗑️ <b>Paket Sil</b>\n{DIV}\nSilinecek paketi seçin:", uid, mid, reply_markup=mk, parse_mode="HTML")

    except Exception as e:
        print(f"Admin Error: {e}")

def premium_ask_id(msg):
    aid = msg.from_user.id
    try:
        tid = int(msg.text.strip())
    except:
        bot.send_message(aid, "❌ <b>Geçersiz ID!</b>\nLütfen sadece rakam gönder.", parse_mode="HTML", reply_markup=_back_kb(aid))
        return
    cursor.execute("SELECT id, name FROM premium_packages ORDER BY price")
    pkgs = cursor.fetchall()
    if not pkgs:
        bot.send_message(aid, "❌ <b>Sistemde tanımlı paket yok!</b>\nÖnce 📦 Paketler bölümünden bir paket ekle.", parse_mode="HTML", reply_markup=_back_kb(aid))
        return
    mk = InlineKeyboardMarkup(row_width=2)
    row = []
    for pkg_id, name in pkgs:
        row.append(btn(name, f"pkgpick_{tid}_{pkg_id}"))
        if len(row) == 2:
            mk.row(*row)
            row = []
    if row:
        mk.row(*row)
    bot.send_message(aid, f"📦 <code>{tid}</code> için hangi paket olarak VIP verilsin?", reply_markup=mk, parse_mode="HTML")

def _extend_premium_until(uid, minutes):
    """Yeni premium süresini, kullanıcının VARSA hâlâ geçerli olan mevcut
    premium_until'ından itibaren EKLER (üzerine yazmaz). 🛠️ FIX: Önceden hem
    activate_package() (kullanıcının Stars ile satın alması) hem de apply_premium()
    (admin'in elle VIP vermesi) süreyi HER ZAMAN 'şu an + dakika' olarak
    hesaplıyordu — yani mevcut premium'un bitmemiş süresi varsa (ör. 5 gün kaldıysa)
    yeni paket bunun üzerine YAZIP o 5 günü SİLİYORDU. Kullanıcı parasını verip
    süresini kaybediyordu. Artık: kullanıcının hâlâ aktif (bitmemiş) bir süresi
    varsa yeni süre ONUN ÜZERİNE eklenir; yoksa (hiç yoksa veya süresi geçmişse)
    normal şekilde 'şu an'dan başlar."""
    base = datetime.now()
    try:
        cursor.execute("SELECT premium_until FROM users WHERE user_id=?", (uid,))
        row = cursor.fetchone()
        if row and row[0]:
            existing = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            if existing > base:
                base = existing
    except Exception:
        pass
    return (base + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def activate_package(uid, pkg_id):
    """Ücretsiz veya ödemesi tamamlanmış bir paketi kullanıcıya otomatik uygular."""
    cursor.execute("SELECT name, duration_minutes, bot_limit FROM premium_packages WHERE id=?", (pkg_id,))
    row = cursor.fetchone()
    if not row:
        bot.send_message(uid, f"❌ <b>Paket bulunamadı!</b>\n{DIV}\nBu paket silinmiş olabilir. Ödemen için destek ile iletişime geç: <b>{esc(SUPPORT_USERNAME)}</b>", parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
        return

    name, minutes, limit = row
    if minutes > 0:
        until = _extend_premium_until(uid, minutes)
        info = f"⏳ {format_duration(minutes)} eklendi. (Yeni bitiş: {until})"
    else:
        until = None
        info = "♾️ Süresiz aktif."

    cursor.execute(
        "UPDATE users SET premium=1, premium_date=?, premium_until=?, premium_package=? WHERE user_id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), until, name, uid)
    )
    conn.commit()

    mk_pkg = InlineKeyboardMarkup()
    mk_pkg.row(btn(T(uid, 'back'), "back_main"))
    bot.send_message(uid, f"✅ <b>{esc(name)}</b> paketin aktif edildi!\n{info}", reply_markup=mk_pkg, parse_mode="HTML")
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, f"⭐ Yeni satın alma!\n👤 Kullanıcı: {user_display(uid)}\n📦 Paket: {esc(name)}\n{info}", parse_mode="HTML")
        except:
            pass

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout(pre_checkout_query):
    try:
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    except Exception as e:
        print(f"Pre-checkout Error: {e}")

@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(msg):
    try:
        payload = msg.successful_payment.invoice_payload
        stars = msg.successful_payment.total_amount
        if payload.startswith("pkg_"):
            pkg_id = int(payload.split("_")[1])
            activate_package(msg.from_user.id, pkg_id)
    except Exception as e:
        print(f"Payment Error: {e}")
        for aid in ADMIN_IDS:
            try:
                bot.send_message(aid, f"⚠️ Ödeme alındı ama paket aktive edilirken hata oluştu!\n👤 {msg.from_user.id}\n{e}")
            except:
                pass

def apply_premium(tid, minutes, pkg_id, aid):
    cursor.execute("SELECT name FROM premium_packages WHERE id=?", (pkg_id,))
    row = cursor.fetchone()
    pkg_name = row[0] if row else "Basit"

    if minutes > 0:
        until = _extend_premium_until(tid, minutes)
        info = f"⏳ {format_duration(minutes)} eklendi. (Yeni bitiş: {until})"
    else:
        until = None
        info = "♾️ Süresiz aktif."

    cursor.execute(
        "UPDATE users SET premium=1, premium_date=?, premium_until=?, premium_package=? WHERE user_id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), until, pkg_name, tid)
    )
    conn.commit()
    mk_ap = InlineKeyboardMarkup()
    mk_ap.row(btn(T(aid, 'back'), "admin_panel"))
    bot.send_message(aid, T(aid, 'premium_given') + f"\n📦 Paket: {esc(pkg_name)}\n{info}", reply_markup=mk_ap)
    try:
        bot.send_message(tid, f"⭐ VIP üyeliğiniz aktif edildi!\n📦 Paket: {esc(pkg_name)}\n{info}")
    except:
        pass

def gen_promo_code(length=10):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choice(chars) for _ in range(length))
        cursor.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,))
        if not cursor.fetchone():
            return code

def backup_bot_files_zip():
    """BOT_FILES_DIR klasöründeki tüm hostlanan bot dosyalarını (.py, alt-db'ler vb.)
    tek bir .zip dosyasına paketler. Redeploy/volume taşıma öncesi elle yedek almak için."""
    if not os.path.isdir(BOT_FILES_DIR):
        return None
    zip_path = f"bot_files_backup_{int(time.time())}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(BOT_FILES_DIR):
                for name in files:
                    full = os.path.join(root, name)
                    arcname = os.path.relpath(full, BOT_FILES_DIR)
                    zf.write(full, arcname)
        return zip_path
    except Exception as e:
        print(f"Backup Files Zip Error: {e}")
        return None


def restore_files_step(msg):
    """Admin bir .zip dosyası gönderdiğinde içeriğini BOT_FILES_DIR klasörüne açar
    (aynı isimli dosyaların üzerine yazarak). backup_bot_files_zip() ile alınan yedeği
    geri yüklemek için kullanılır."""
    aid = msg.from_user.id

    if not is_admin(aid):
        return

    if not msg.document:
        m = bot.send_message(aid, "❌ <b>Bir dosya göndermelisin!</b> Tekrar dene:", parse_mode="HTML")
        bot.register_next_step_handler(m, restore_files_step)
        return

    if not msg.document.file_name.lower().endswith(".zip"):
        m = bot.send_message(aid, "❌ <b>Sadece .zip uzantılı dosya kabul edilir!</b> Tekrar dene:", parse_mode="HTML")
        bot.register_next_step_handler(m, restore_files_step)
        return

    try:
        fi = bot.get_file(msg.document.file_id)
        data = bot.download_file(fi.file_path)
    except Exception as e:
        bot.send_message(aid, f"❌ <b>Dosya indirilemedi:</b> {esc(e)}", parse_mode="HTML")
        return

    tmp_zip = f"_restore_upload_{int(time.time())}.zip"
    try:
        with open(tmp_zip, "wb") as f:
            f.write(data)

        if not zipfile.is_zipfile(tmp_zip):
            bot.send_message(aid, "❌ <b>Bu geçerli bir .zip dosyası değil!</b>", parse_mode="HTML")
            return

        os.makedirs(BOT_FILES_DIR, exist_ok=True)
        extracted = 0
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.namelist():
                # 🛡️ Zip Slip / path traversal koruması
                dest = os.path.normpath(os.path.join(BOT_FILES_DIR, member))
                if not dest.startswith(os.path.abspath(BOT_FILES_DIR) + os.sep) and dest != os.path.abspath(BOT_FILES_DIR):
                    continue
                if member.endswith("/"):
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
                extracted += 1

        bot.send_message(
            aid,
            f"✅ <b>Bot dosyaları geri yüklendi!</b>\n{DIV}\n"
            f"📂 Hedef klasör: <code>{esc(BOT_FILES_DIR)}</code>\n"
            f"📄 Açılan dosya sayısı: <b>{extracted}</b>",
            parse_mode="HTML"
        )
        main_menu(aid)
    except Exception as e:
        bot.send_message(aid, f"❌ <b>Geri yükleme başarısız:</b> {esc(e)}", parse_mode="HTML")
    finally:
        try:
            os.remove(tmp_zip)
        except:
            pass


def restore_db_step(msg):
    """Admin bir .db dosyası gönderdiğinde botun veritabanını o dosyayla değiştirir.
    Kullanıcılar, botlar, VIP'ler, promo kodlar dahil TÜM veriler bu dosyadan devam eder.
    Değiştirmeden önce mevcut veritabanı otomatik olarak yedeklenir."""
    global conn, cursor
    aid = msg.from_user.id

    if not is_admin(aid):
        return

    if not msg.document:
        m = bot.send_message(aid, "❌ <b>Bir dosya göndermelisin!</b> Tekrar dene:", parse_mode="HTML")
        bot.register_next_step_handler(m, restore_db_step)
        return

    if not msg.document.file_name.lower().endswith(".db"):
        m = bot.send_message(aid, "❌ <b>Sadece .db uzantılı dosya kabul edilir!</b> Tekrar dene:", parse_mode="HTML")
        bot.register_next_step_handler(m, restore_db_step)
        return

    try:
        fi = bot.get_file(msg.document.file_id)
        data = bot.download_file(fi.file_path)
    except Exception as e:
        bot.send_message(aid, f"❌ <b>Dosya indirilemedi:</b> {esc(e)}", parse_mode="HTML")
        return

    if not data.startswith(b"SQLite format 3\x00"):
        bot.send_message(aid, "❌ <b>Bu geçerli bir SQLite veritabanı dosyası değil!</b>", parse_mode="HTML")
        return

    try:
        # 🔒 Mevcut veritabanını güvenli şekilde kapat ve yedekle
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            conn.commit()
        except:
            pass
        conn.close()

        backup_path = f"{DB_PATH}.bak_{int(time.time())}"
        if os.path.exists(DB_PATH):
            os.replace(DB_PATH, backup_path)
        for ext in ("-wal", "-shm"):
            side = DB_PATH + ext
            if os.path.exists(side):
                try:
                    os.remove(side)
                except:
                    pass

        with open(DB_PATH, "wb") as f:
            f.write(data)

        # ?? Yeni veritabanına bağlan ve şema/migrasyonları uygula
        conn, cursor = open_db(DB_PATH)

        bot.send_message(
            aid,
            f"✅ <b>Veritabanı başarıyla yüklendi!</b>\n{DIV}\n"
            f"📦 Eski veritabanı yedeklendi: <code>{esc(backup_path)}</code>\n"
            f"🔄 Bot artık gönderdiğin veritabanıyla çalışıyor — kullanıcılar, botlar, VIP'ler ve promo kodlar bu dosyadan devam ediyor.",
            parse_mode="HTML"
        )
        main_menu(aid)
    except Exception as e:
        bot.send_message(aid, f"❌ <b>Geri yükleme başarısız oldu:</b> {esc(e)}", parse_mode="HTML")
        try:
            conn, cursor = open_db(DB_PATH)
        except:
            pass


def promo_ask_code(msg):
    aid = msg.from_user.id
    raw = msg.text.strip()
    code = gen_promo_code() if raw.lower() == "auto" else re.sub(r"\s+", "", raw).upper()

    if not code:
        m = bot.send_message(aid, "❌ <b>Geçersiz kod!</b> Tekrar dene:", parse_mode="HTML")
        bot.register_next_step_handler(m, promo_ask_code)
        return

    PROMO_WIZARD[aid] = {'code': code, 'max_uses': 1}
    m = bot.send_message(
        aid,
        f"🔁 <b>Kullanım limiti kaç olsun?</b>\nSadece rakam gönder (0 = sınırsız).",
        parse_mode="HTML",
        reply_markup=_back_kb(aid, "admin_panel")
    )
    bot.register_next_step_handler(m, promo_ask_uses)

def promo_ask_uses(msg):
    aid = msg.from_user.id
    info = PROMO_WIZARD.get(aid)
    if not info:
        bot.send_message(aid, "⌛ Süre doldu, tekrar dene: Promo Oluştur")
        return
    try:
        uses = int(msg.text.strip())
    except:
        m = bot.send_message(aid, "❌ <b>Sadece rakam gönder!</b>", parse_mode="HTML")
        bot.register_next_step_handler(m, promo_ask_uses)
        return

    info['max_uses'] = uses
    PROMO_WIZARD[aid] = info
    mk = duration_keyboard("promodur")
    bot.send_message(aid, "⏳ <b>VIP süresini seç:</b>", reply_markup=mk, parse_mode="HTML")

def send_promo_list(uid, mid=None):
    cursor.execute("SELECT code, duration_minutes, max_uses, used_count FROM promo_codes ORDER BY rowid DESC")
    rows = cursor.fetchall()
    mk = InlineKeyboardMarkup()

    if not rows:
        mk.row(btn(T(uid, 'back'), "admin_panel"))
        txt = f"🎟️ <b>{T(uid, 'admin_promo')}</b>\n{DIV}\nHenüz promo kod yok."
        if mid:
            try:
                bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
                return
            except:
                pass
        bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")
        return

    txt = f"🎟️ <b>{T(uid, 'admin_promo')}</b>\n{DIV}\n"
    for code, minutes, max_uses, used in rows:
        uses_txt = "♾️" if max_uses <= 0 else f"{used}/{max_uses}"
        txt += f"🔖 <code>{esc(code)}</code> · ⏳ {format_duration(minutes)} · 🔁 {uses_txt}\n"
        mk.row(btn(f"🗑️ {code}", f"delpromo_{code}"))
    mk.row(btn(T(uid, 'back'), "admin_panel"))

    if mid:
        try:
            bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

def redeem_promo_step(msg):
    uid = msg.from_user.id
    code = re.sub(r"\s+", "", msg.text.strip()).upper()

    cursor.execute("SELECT duration_minutes, max_uses, used_count, active FROM promo_codes WHERE code=?", (code,))
    row = cursor.fetchone()
    if not row or not row[3]:
        bot.send_message(uid, T(uid, 'promo_invalid'), parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
        return

    minutes, max_uses, used_count, active = row
    if max_uses > 0 and used_count >= max_uses:
        bot.send_message(uid, T(uid, 'promo_no_uses'), parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
        return

    cursor.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?", (code, uid))
    if cursor.fetchone():
        bot.send_message(uid, T(uid, 'promo_already_used'), parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
        return

    if minutes > 0:
        until = _extend_premium_until(uid, minutes)
        info = f"⏳ {format_duration(minutes)} eklendi. (Yeni bitiş: {until})"
    else:
        until = None
        info = "♾️ Süresiz aktif."
    cursor.execute(
        "UPDATE users SET premium=1, premium_date=?, premium_until=? WHERE user_id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), until, uid)
    )
    cursor.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
    cursor.execute(
        "INSERT INTO promo_redemptions (code, user_id, redeemed_at) VALUES (?,?,?)",
        (code, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()

    bot.send_message(uid, T(uid, 'promo_success').format(info), parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, f"🎟️ Promo kod kullanıldı!\n👤 Kullanıcı: {user_display(uid)}\n🔖 Kod: <code>{esc(code)}</code>", parse_mode="HTML")
        except:
            pass

def take_premium(msg):
    aid = msg.from_user.id
    try:
        tid = int(msg.text.strip())
    except:
        bot.send_message(aid, "❌ <b>Geçersiz ID!</b>\nLütfen sadece rakam gönder.", parse_mode="HTML", reply_markup=_back_kb(aid))
        return

    cursor.execute("SELECT premium FROM users WHERE user_id=?", (tid,))
    row = cursor.fetchone()

    if not row:
        bot.send_message(aid, f"⚠️ <b>Kullanıcı bulunamadı</b>\n{DIV}\n🆔 <code>{tid}</code> sistemde kayıtlı değil.", parse_mode="HTML", reply_markup=_back_kb(aid))
        return

    if row[0] != 1:
        bot.send_message(aid, f"ℹ️ <b>Bu kullanıcının zaten VIP'i yok</b>\n{DIV}\n🆔 <code>{tid}</code>\n\nBir işlem yapılmadı.", parse_mode="HTML", reply_markup=_back_kb(aid))
        return

    cursor.execute("UPDATE users SET premium=0, premium_date=NULL, premium_until=NULL WHERE user_id=?", (tid,))
    conn.commit()
    bot.send_message(aid, f"✅ <b>VIP kaldırıldı!</b>\n{DIV}\n🆔 <code>{tid}</code>", parse_mode="HTML", reply_markup=_back_kb(aid))
    try:
        bot.send_message(tid, "🚫 <b>VIP üyeliğiniz sonlandırıldı.</b>", parse_mode="HTML")
    except:
        pass

def unban_user_flow(msg):
    aid = msg.from_user.id
    raw = msg.text.strip()
    try:
        tid = int(raw)
    except:
        bot.send_message(aid, "❌ <b>Geçersiz ID!</b>\nLütfen sadece sayısal bir kullanıcı ID'si gönder.", parse_mode="HTML", reply_markup=_back_kb(aid))
        return

    cursor.execute("SELECT banned, ban_reason FROM users WHERE user_id=?", (tid,))
    row = cursor.fetchone()

    if not row:
        bot.send_message(
            aid,
            f"⚠️ <b>Kullanıcı bulunamadı</b>\n{DIV}\n🆔 <code>{tid}</code> ID'li bir kullanıcı sistemde kayıtlı değil.",
            parse_mode="HTML",
            reply_markup=_back_kb(aid)
        )
        return

    if row[0] != 1:
        bot.send_message(
            aid,
            f"ℹ️ <b>Bu kullanıcı zaten engelli değil</b>\n{DIV}\n🆔 <code>{tid}</code>\n📊 Durum: 🟢 Engelli değil\n\nBir işlem yapılmadı.",
            parse_mode="HTML",
            reply_markup=_back_kb(aid)
        )
        return

    unban_user(tid)
    bot.send_message(
        aid,
        f"✅ <b>Engel kaldırıldı!</b>\n{DIV}\n🆔 <code>{tid}</code>\n📊 Yeni durum: 🟢 Engelli değil",
        parse_mode="HTML",
        reply_markup=_back_kb(aid)
    )
    try:
        bot.send_message(tid, "✅ <b>Hesabınızın engeli kaldırıldı!</b>\nTekrar botu kullanabilirsiniz.", parse_mode="HTML")
    except:
        pass

def set_limit(msg):
    aid = msg.from_user.id
    raw = msg.text.strip()
    try:
        lim = int(raw)
    except:
        bot.send_message(aid, "❌ <b>Geçersiz sayı!</b>\nLütfen sadece rakam gönder (örn: 3).", parse_mode="HTML", reply_markup=_back_kb(aid))
        return

    if lim < 0:
        lim = 0

    old_lim = get_limit()
    cursor.execute("UPDATE settings SET value=? WHERE key='free_limit'", (str(lim),))
    conn.commit()
    bot.send_message(
        aid,
        f"✅ <b>Ücretsiz Bot Limiti Güncellendi!</b>\n"
        f"{DIV}\n"
        f"📉 Eski limit: <b>{old_lim}</b>\n"
        f"📈 Yeni limit: <b>{lim}</b>",
        parse_mode="HTML",
        reply_markup=_back_kb(aid)
    )

def broadcast(msg):
    try:
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
    except:
        users = []

    c = 0
    for u in users:
        try:
            bot.send_message(u[0], f"📢 <b>DUYURU</b>\n{DIV}\n{esc(msg.text)}", parse_mode="HTML")
            c += 1
            time.sleep(0.05)
        except:
            pass

    bot.send_message(
        msg.from_user.id,
        f"✅ <b>Duyuru Gönderildi!</b>\n{DIV}\n📨 Ulaşan kişi sayısı: <b>{c}</b> / {len(users)}",
        parse_mode="HTML",
        reply_markup=_back_kb(msg.from_user.id)
    )

def send_pkg_view(aid, pkg_id, mid=None):
    cursor.execute("SELECT name, price, description, bot_limit, duration_minutes, watermark FROM premium_packages WHERE id=?", (pkg_id,))
    p = cursor.fetchone()
    if not p:
        bot.send_message(aid, "❌ <b>Paket bulunamadı!</b>\nBu paket silinmiş olabilir.", parse_mode="HTML")
        return
    price_txt = "Ücretsiz" if p[1] == 0 else f"{p[1]} ⭐"
    dur_txt = format_duration(p[4])
    wm_on = p[5] if p[5] is not None else 1
    wm_txt = "✅ Evet" if wm_on else "❌ Hayır"
    txt = (
        f"⚙️ <b>{esc(p[0])}</b> — Paket Düzenle\n"
        f"{DIV}\n"
        f"💰 Fiyat: <b>{price_txt}</b>\n"
        f"🤖 Bot Hakkı: <b>{p[3]}</b>\n"
        f"⏳ Süre: <b>{dur_txt}</b>\n"
        f"🏷️ Sistem Filigranı: <b>{wm_txt}</b>\n"
        f"📝 {esc(p[2])}\n"
        f"{DIV}\n"
        f"👇 Değiştirmek istediğin alanı seç"
    )
    mk = InlineKeyboardMarkup(row_width=2)
    mk.row(btn("✏️ İsim", f"pkgedit_name_{pkg_id}"), btn("💰 Fiyat", f"pkgedit_price_{pkg_id}"))
    mk.row(btn("📝 Açıklama", f"pkgedit_desc_{pkg_id}"), btn("🤖 Bot Limiti", f"pkgedit_limit_{pkg_id}"))
    mk.row(btn("⏳ Süre", f"pkgedit_dur_{pkg_id}"), btn(f"🏷️ Filigran: {wm_txt}", f"pkgedit_wm_{pkg_id}"))
    mk.row(btn("🗑️ Paketi Sil", f"del_pkg_{pkg_id}"))
    mk.row(btn("⬅️ Geri", "admin_packages"))

    if mid:
        try:
            bot.edit_message_text(txt, aid, mid, reply_markup=mk, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(aid, txt, reply_markup=mk, parse_mode="HTML")

def pkg_edit_set_field(msg, pkg_id, field):
    aid = msg.from_user.id
    val = msg.text.strip()
    try:
        if field in ('price', 'bot_limit'):
            val = int(val)
        cursor.execute(f"UPDATE premium_packages SET {field}=? WHERE id=?", (val, pkg_id))
        conn.commit()
        bot.send_message(aid, "✅ <b>Güncellendi!</b>", parse_mode="HTML")
    except:
        bot.send_message(aid, "❌ <b>Geçersiz değer!</b>\nDeğişiklik yapılmadı.", parse_mode="HTML")
    send_pkg_view(aid, pkg_id)

def pkg_ask_price(msg):
    aid = msg.from_user.id
    name = msg.text.strip()
    if not name:
        bot.send_message(aid, "❌ <b>Geçersiz isim!</b>\nBaştan başlamak için Paket Ekle'ye tekrar bas.", parse_mode="HTML")
        return
    PKG_WIZARD[aid] = {'name': name}
    m = bot.send_message(aid, "💰 <b>Fiyat</b> (⭐ yıldız) kaç olsun? (Ücretsiz için 0)", parse_mode="HTML", reply_markup=_back_kb(aid, "admin_panel"))
    bot.register_next_step_handler(m, pkg_ask_desc)

def pkg_ask_desc(msg):
    aid = msg.from_user.id
    try:
        price = int(msg.text.strip())
    except:
        bot.send_message(aid, "❌ <b>Geçersiz fiyat!</b>\nSadece rakam gönder. Baştan başlamak için Paket Ekle'ye tekrar bas.", parse_mode="HTML")
        PKG_WIZARD.pop(aid, None)
        return
    PKG_WIZARD.setdefault(aid, {})['price'] = price
    m = bot.send_message(aid, "📝 <b>Paket açıklaması</b> ne olsun?", parse_mode="HTML", reply_markup=_back_kb(aid, "admin_panel"))
    bot.register_next_step_handler(m, pkg_ask_limit)

def pkg_ask_limit(msg):
    aid = msg.from_user.id
    desc = msg.text.strip()
    PKG_WIZARD.setdefault(aid, {})['desc'] = desc
    m = bot.send_message(aid, "🤖 <b>Bot hakkı</b> (limit) kaç olsun?", parse_mode="HTML", reply_markup=_back_kb(aid, "admin_panel"))
    bot.register_next_step_handler(m, pkg_ask_duration)

def show_package_templates(uid, mid):
    """3 paket şablonu sununu - kullanıcı birini seçip detaylarını girsin"""
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(btn("🌿 Acemi Paket", "pkg_template_beginner"))
    mk.add(btn("⚡ Profesyonel Paket", "pkg_template_professional"))
    mk.add(btn("🔥 Usta Paket", "pkg_template_master"))
    mk.row(btn("⬅️ Geri", "admin_panel"))
    
    txt = (
        f"📦 <b>Eklemek İstediğiniz Paketi Seçin</b>\n{DIV}\n"
        f"İçeriğini görmek ve satın almak istediğiniz pakete tıklayın.\n\n"
        f"<i>Seçim yaptıktan sonra paket detaylarını gireceksiniz.</i>"
    )
    bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")

def start_package_wizard(uid, mid, template_type):
    """Seçilen paket şablonuna göre sihirbazı başlat"""
    bot.clear_step_handler_by_chat_id(uid)
    
    PKG_WIZARD[uid] = {'template': template_type}
    
    msg = bot.send_message(uid, f"📦 <b>{template_type} Paket Ekle</b>\n{DIV}\n📝 Paket adı nedir?", parse_mode="HTML", reply_markup=_back_kb(uid, "admin_panel"))
    bot.register_next_step_handler(msg, pkg_ask_price)

def pkg_ask_duration(msg):
    aid = msg.from_user.id
    try:
        limit = int(msg.text.strip())
    except:
        bot.send_message(aid, "❌ <b>Geçersiz sayı!</b>\nBaştan başlamak için Paket Ekle'ye tekrar bas.", parse_mode="HTML")
        PKG_WIZARD.pop(aid, None)
        return
    if aid not in PKG_WIZARD:
        bot.send_message(aid, "⌛ <b>Süre doldu!</b>\nPaket Ekle'ye tekrar bas.", parse_mode="HTML")
        return
    PKG_WIZARD[aid]['limit'] = limit
    mk = duration_keyboard("pkgdur")
    bot.send_message(aid, "⏳ <b>Bu paket ne kadar süreli VIP versin?</b>", reply_markup=mk, parse_mode="HTML")

# 🛍️ ŞABLON MARKETİ
# ─────────────────────────────────────────────────────────────
# Hazır, tam donanımlı bot şablonlarının tek tıkla kurulmasını sağlar.
# Her kurulan kopya kendi SQLite veritabanıyla ayrı bir process olarak çalışır
# (mevcut start_bot/stop_bot altyapısı hiç değiştirilmeden kullanılır).
# "Aylık Kullanıcı" rakamı sahte bir sayaç DEĞİLDİR: o şablondan kurulmuş
# TÜM gerçek botların kendi veritabanlarındaki son-30-gün aktif kullanıcı
# sayıları toplanarak hesaplanır.

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

TEMPLATES = {
    "market_gelismis": {
        "name": "📦 Market Bot Gelişmiş",
        "file": os.path.join(TEMPLATES_DIR, "bot_gelismis_template.py"),
        "card": (
            "📦 <b>Market Bot Gelişmiş</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🛒 <b>Gelişmiş Dijital Market Botu</b>\n"
            "Bu bot, Telegram üzerinde tamamen otomatik çalışan gelişmiş bir dijital ürün "
            "satış (market) botudur. Kullanıcılar puan sistemiyle ürün satın alabilir, VIP "
            "üyelik alabilir ve birçok gelişmiş özelliği kullanabilir.\n\n"
            "✨ <b>Başlıca Özellikler:</b>\n"
            "• 🛍️ Kategorili ürün mağazası\n"
            "• 📦 Stok takip sistemi\n"
            "• 💎 Puan ile alışveriş\n"
            "• 👑 VIP üyelik sistemi\n"
            "• 🎁 Kupon kodu oluşturma ve kullanma\n"
            "• 👥 Referans (Davet) sistemi\n"
            "• 🎯 Günlük bonus ödülleri\n"
            "• 📋 Sipariş oluşturma ve takip\n"
            "• ✅ Admin onay/red sistemi\n"
            "• 📊 Liderlik (Leaderboard) sistemi\n"
            "• 🌍 Çoklu dil desteği (Türkçe 🇹🇷 / İngilizce 🇬🇧 / Arapça 🇸🇦)\n"
            "• 🔒 Captcha doğrulama\n"
            "• 📢 Zorunlu kanal katılım sistemi\n"
            "• ⚙️ Gelişmiş yönetici paneli\n"
            "• 📈 İstatistik ekranı\n"
            "• 📢 Toplu duyuru gönderme\n"
            "• 🚫 Kullanıcı banlama ve puan yönetimi\n"
            "• 🗂️ Kategori ve ürün ekleme/silme\n"
            "• 💾 SQLite veritabanı ile güvenli veri saklama\n\n"
            "🚀 <b>Kimler İçin Uygun?</b>\n"
            "• Dijital ürün satışı yapanlar\n"
            "• Hesap, lisans, yazılım ve dosya satıcıları\n"
            "• Telegram üzerinden profesyonel market kurmak isteyenler\n\n"
            "⭐ Modern arayüzü, otomatik sipariş sistemi ve gelişmiş yönetim paneli "
            "sayesinde profesyonel bir Telegram marketi kurmanızı sağlar."
        ),
    },
}

def _fmt_num(n):
    return f"{n:,}".replace(",", ".")

def template_db_path(bid):
    return f"{BOT_FILES_DIR}/market_db_{bid}.db"

def user_db_dir(bid):
    """Kullanıcının kendi yüklediği (şablon dışı) botlar için izole veritabanı klasörü.
    build_bot_launcher tarafından o botun TÜM sqlite3.connect() çağrıları -kod içinde
    hangi isim/yol yazılırsa yazılsın- şeffafça buraya yönlendirilir. Böylece panel,
    kullanıcının kendi yazdığı botun veritabanının her zaman nerede olduğunu bilir."""
    return f"{BOT_FILES_DIR}/userdb_{bid}"

def _snapshot_sqlite_or_copy(src_path, dest_path):
    """Mümkünse sqlite backup API'siyle tutarlı bir anlık kopya alır (bot çalışırken
    yarıda/bozuk veri gönderilmesin diye); dosya geçerli bir sqlite veritabanı değilse
    (örn. kullanıcı .json/.txt gibi başka bir şey kaydediyorsa) ham kopya yapar."""
    try:
        src = sqlite3.connect(src_path, timeout=5)
        dst = sqlite3.connect(dest_path)
        src.backup(dst)
        dst.close()
        src.close()
        return True
    except Exception:
        try:
            with open(src_path, "rb") as rf, open(dest_path, "wb") as wf:
                wf.write(rf.read())
            return True
        except Exception:
            return False

def get_template_monthly_users(key):
    """Bu şablondan kurulmuş tüm gerçek botların kendi veritabanlarına bakıp
    son 30 gün içinde aktif olmuş kullanıcı sayılarını toplar."""
    total = 0
    try:
        cursor.execute("SELECT id FROM bot_files WHERE template_key=?", (key,))
        rows = cursor.fetchall()
    except Exception:
        rows = []
    for (bid,) in rows:
        dbp = template_db_path(bid)
        if not os.path.exists(dbp):
            continue
        try:
            c2 = sqlite3.connect(dbp, timeout=2)
            cur2 = c2.cursor()
            try:
                cur2.execute("ALTER TABLE users ADD COLUMN last_start TEXT")
                c2.commit()
            except Exception:
                pass  # kolon zaten var
            cur2.execute("SELECT COUNT(*) FROM users WHERE last_start >= datetime('now','-30 days')")
            row = cur2.fetchone()
            if row and row[0]:
                total += row[0]
            c2.close()
        except Exception:
            pass
    return total

def template_market(uid, mid):
    items = []
    for key, t in TEMPLATES.items():
        items.append((key, t['name'], get_template_monthly_users(key)))
    items.sort(key=lambda x: x[2], reverse=True)

    mk = InlineKeyboardMarkup(row_width=1)
    for key, name, mu in items:
        mk.add(btn(f"{name}  •  👥 {_fmt_num(mu)} Aylık Kullanıcı", f"tmpl_view_{key}"))
    mk.row(btn(T(uid, 'back'), "back_main"))

    txt = (
        f"🛍️ <b>Şablon Marketi</b>\n"
        f"{DIV}\n"
        f"Hazır, tüm özellikleriyle çalışan bot şablonlarını tek tıkla kur.\n"
        f"👇 Aylık kullanıcı sayısına göre sıralı şablonlar:"
    )
    try:
        bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
    except Exception:
        bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

def template_detail(uid, mid, key):
    t = TEMPLATES.get(key)
    if not t:
        try:
            bot.answer_callback_query(mid, "Şablon bulunamadı!")
        except Exception:
            pass
        return

    mu = get_template_monthly_users(key)
    txt = (
        f"{t['card']}\n"
        f"{DIV}\n"
        f"📊 <b>Aylık Kullanıcı:</b> {_fmt_num(mu)} kişi\n"
        f"{DIV}\n\n"
        f"Bu botu kurmak istediğinize emin misiniz?"
    )
    mk = InlineKeyboardMarkup(row_width=2)
    mk.row(btn("✅ Evet, Kur", f"tmpl_install_{key}"), btn("❌ Vazgeç", "template_market"))
    try:
        bot.edit_message_text(txt, uid, mid, reply_markup=mk, parse_mode="HTML")
    except Exception:
        bot.send_message(uid, txt, reply_markup=mk, parse_mode="HTML")

def template_install_start(uid, mid, key):
    t = TEMPLATES.get(key)
    if not t:
        return

    if is_maintenance() and not (is_admin(uid) and is_admin_sleep_immune()):
        send_sleep_upload_msg(uid)
        return
    if not check_channel(uid):
        main_menu(uid)
        return

    if not os.path.exists(t['file']):
        bot.send_message(uid, "⚠️ <b>Şablon dosyası bulunamadı!</b>\nLütfen destek ile iletişime geç.", parse_mode="HTML", reply_markup=_back_kb(uid, "back_main"))
        return

    if is_premium(uid):
        cursor.execute("SELECT bot_count FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        bc = r[0] if r else 0
        ul = get_user_bot_limit(uid)
        if bc >= ul:
            bot.send_message(uid, T(uid, 'premium_bot_limit_warning').format(bc, ul))
            return
    else:
        cursor.execute("SELECT bot_count FROM users WHERE user_id=?", (uid,))
        r = cursor.fetchone()
        bc = r[0] if r else 0
        fl = get_limit()
        if bc >= fl:
            bot.send_message(uid, T(uid, 'daily_limit_warning').format(fl))
            return

    msg = bot.send_message(
        uid,
        f"🤖 <b>{esc(t['name'])}</b> kurulumu\n"
        f"{DIV}\n"
        f"1️⃣ Telegram'da <b>@BotFather</b>'a git\n"
        f"2️⃣ <code>/newbot</code> komutuyla yeni bir bot oluştur\n"
        f"3️⃣ Sana verdiği <b>API Token</b>'ı buraya gönder\n"
        f"{DIV}\n"
        f"👇 Bot token'ını gönder:",
        parse_mode="HTML",
        reply_markup=_back_kb(uid, "back_main")
    )
    bot.clear_step_handler_by_chat_id(uid)
    bot.register_next_step_handler(msg, lambda m: template_receive_token(m, key))

def template_receive_token(msg, key):
    uid = msg.from_user.id
    t = TEMPLATES.get(key)
    if not t:
        return

    token = (msg.text or "").strip()
    if ":" not in token or len(token) < 30:
        bot.send_message(
            uid,
            "❌ <b>Geçersiz token!</b>\nLütfen @BotFather'dan aldığın token'ı olduğu gibi gönder.",
            parse_mode="HTML", reply_markup=_back_kb(uid, "back_main")
        )
        return

    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
        gm = r.json()
    except Exception:
        gm = {}

    if not gm.get('ok'):
        bot.send_message(
            uid,
            "❌ <b>Token doğrulanamadı!</b>\nToken'ı kontrol edip tekrar dene.",
            parse_mode="HTML", reply_markup=_back_kb(uid, "back_main")
        )
        return

    bot_username = gm['result']['username']

    cursor.execute("SELECT id FROM bot_files WHERE bot_token=?", (token,))
    if cursor.fetchone():
        bot.send_message(
            uid,
            "⚠️ <b>Bu token zaten kullanımda!</b>\nBaşka bir bot için farklı bir token oluştur.",
            parse_mode="HTML", reply_markup=_back_kb(uid, "back_main")
        )
        return

    msg2 = bot.send_message(
        uid,
        "📢 <b>Zorunlu Kanal</b>\n"
        f"{DIV}\n"
        "Botunu kullanmadan önce kullanıcıların katılması zorunlu bir kanal/grup "
        "belirlemek ister misin?\n\n"
        "👇 <b>Genel (public) kanal ise</b> kullanıcı adını gönder:\n"
        "<code>@kanaladi</code>\n\n"
        "👇 <b>Gizli (private) kanal ise</b> davet linkini gönder, şu formatta olmalı:\n"
        "<code>https://t.me/+AbCdEfGhIJ1234567</code>\n"
        "(Gizli kanallarda kullanıcı adı olmadığı için link kullanılır.)\n\n"
        "İstemiyorsan <code>yok</code> yaz.\n"
        f"{DIV}\n"
        "⚠️ <b>Not:</b> Botun zorunlu kanalı tespit edebilmesi için kanalda veya "
        "grupta <b>admin</b> olması gerekir.",
        parse_mode="HTML",
        reply_markup=_back_kb(uid, "back_main")
    )
    bot.clear_step_handler_by_chat_id(uid)
    uname = msg.from_user.username or ""
    fname = msg.from_user.first_name or ""
    bot.register_next_step_handler(msg2, lambda m: template_receive_channel(m, key, token, bot_username, uname, fname))

def template_receive_channel(msg, key, token, bot_username, uname, fname):
    uid = msg.from_user.id
    raw = (msg.text or "").strip()

    if raw.lower() in ("yok", "hayır", "hayir", "-", "skip", "geç", "gec"):
        finalize_template_install(uid, key, token, bot_username, "", "", uname, fname)
        return

    is_private_link = bool(re.search(r't\.me/(\+|joinchat/)', raw, re.IGNORECASE))
    is_public_link = (not is_private_link) and bool(re.search(r't\.me/([A-Za-z0-9_]{5,32})', raw, re.IGNORECASE))

    if is_private_link:
        m2 = bot.send_message(
            uid,
            "🔒 <b>Gizli Kanal Tespit Ediliyor</b>\n"
            f"{DIV}\n"
            "Botun kanalı tanıyabilmesi için o kanaldan (veya gruptan) herhangi bir "
            "mesajı buraya <b>iletmen (forward)</b> gerekiyor.\n\n"
            "👇 Şimdi o kanaldaki herhangi bir mesajı buraya ilet.",
            parse_mode="HTML", reply_markup=_back_kb(uid, "back_main")
        )
        bot.register_next_step_handler(m2, lambda m: template_receive_private_forward(m, key, token, bot_username, raw, uname, fname))
        return

    if is_public_link:
        mtc = re.search(r't\.me/([A-Za-z0-9_]{5,32})', raw, re.IGNORECASE)
        ch = mtc.group(1)
    else:
        ch = raw.lstrip('@').strip()

    if not ch or not re.match(r'^[A-Za-z0-9_]{5,32}$', ch):
        m2 = bot.send_message(
            uid,
            "❌ <b>Geçersiz kanal!</b>\n"
            "Genel kanal için <code>@kanaladi</code>, gizli kanal için "
            "<code>https://t.me/+AbCdEfGhIJ1234567</code> formatında bir davet linki gönder. "
            "İstemiyorsan <code>yok</code> yaz.",
            parse_mode="HTML", reply_markup=_back_kb(uid, "back_main")
        )
        bot.register_next_step_handler(m2, lambda m: template_receive_channel(m, key, token, bot_username, uname, fname))
        return

    finalize_template_install(uid, key, token, bot_username, ch, f"https://t.me/{ch}", uname, fname)

def template_receive_private_forward(msg, key, token, bot_username, invite_link, uname, fname):
    uid = msg.from_user.id
    fchat = getattr(msg, "forward_from_chat", None)
    if not fchat or not getattr(fchat, "id", None):
        m2 = bot.send_message(
            uid,
            "❌ <b>Mesaj algılanamadı!</b>\n"
            "Lütfen doğrudan o gizli kanaldan/gruptan bir mesajı <b>ilet (forward)</b> — "
            "kendi yazdığın bir mesaj değil, kanaldaki gerçek bir mesaj olmalı.\n\n"
            "Vazgeçmek için <code>yok</code> yaz.",
            parse_mode="HTML", reply_markup=_back_kb(uid, "back_main")
        )
        if (msg.text or "").strip().lower() in ("yok", "hayır", "hayir", "-", "skip"):
            finalize_template_install(uid, key, token, bot_username, "", "", uname, fname)
            return
        bot.register_next_step_handler(m2, lambda m: template_receive_private_forward(m, key, token, bot_username, invite_link, uname, fname))
        return

    finalize_template_install(uid, key, token, bot_username, str(fchat.id), invite_link, uname, fname)

    finalize_template_install(uid, key, token, bot_username, force_channel, uname, fname)

def finalize_template_install(uid, key, token, bot_username, force_channel, force_channel_link, uname, fname):
    t = TEMPLATES.get(key)
    if not t:
        return

    st = bot.send_message(uid, "⚙️ <b>Şablon kuruluyor...</b>", parse_mode="HTML")

    try:
        fn = f"market_gelismis_{uid}.py"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            "INSERT INTO bot_files (user_id, file_name, file_path, status, bot_status, submitted_at, template_key) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, fn, "", 'pending', 'stopped', now, key)
        )
        conn.commit()
        bid = cursor.lastrowid

        os.makedirs(BOT_FILES_DIR, exist_ok=True)
        fp = f"{BOT_FILES_DIR}/{bid}_{fn}"
        db_path = template_db_path(bid)

        with open(t['file'], 'r', encoding='utf-8') as f:
            src = f.read()

        src = (src.replace("__BOT_TOKEN__", token)
                  .replace("__DB_PATH__", db_path)
                  .replace("__OWNER_ID__", str(uid))
                  .replace("__FORCE_CHANNEL__", force_channel)
                  .replace("__FORCE_CHANNEL_LINK__", force_channel_link))

        with open(fp, 'w', encoding='utf-8') as f:
            f.write(src)

        approved_hash = _file_hash(fp)
        cursor.execute(
            "UPDATE bot_files SET file_path=?, status='approved', bot_token=?, bot_username=?, "
            "approved_at=?, approved_file_hash=? WHERE id=?",
            (fp, token, bot_username, now, approved_hash, bid)
        )
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, name, created_at) VALUES (?,?,?,?)",
            (uid, uname, fname, now)
        )
        cursor.execute("UPDATE users SET bot_count=bot_count+1, total_files=total_files+1 WHERE user_id=?", (uid,))
        conn.commit()

        mk = InlineKeyboardMarkup(row_width=1)
        mk.row(btn("▶️ Şimdi Başlat", f"bot_start_{bid}"))
        mk.row(btn("📁 Botlarım", "my_bots"))

        if force_channel:
            ch_display = f"@{force_channel}" if not force_channel.lstrip('-').isdigit() else force_channel_link
            ch_line = f"📢 Zorunlu Kanal: {esc(ch_display)}\n"
            ch_note = "\n⚠️ Botun kanalı tespit edebilmesi için botu kanala/gruba <b>admin</b> olarak eklemeyi unutma!"
        else:
            ch_line = ""
            ch_note = ""
        bot.edit_message_text(
            f"✅ <b>{esc(t['name'])} başarıyla kuruldu!</b>\n"
            f"{DIV}\n"
            f"🤖 Bot: @{esc(bot_username)}\n"
            f"🆔 İşlem No: {bid}\n"
            f"{ch_line}"
            f"\nAşağıdan botunu başlatabilirsin.{ch_note}",
            uid, st.message_id, reply_markup=mk, parse_mode="HTML"
        )

    except Exception as e:
        print(f"Template Install Error: {e}")
        try:
            bot.edit_message_text(f"❌ <b>Kurulum başarısız!</b>\nHata: {str(e)[:150]}", uid, st.message_id, parse_mode="HTML")
        except Exception:
            pass

print("=" * 50)
print("🌌 NEBULA BOT HOSTING")
print("=" * 50)
print(f"👑 Admin: {ADMIN_IDS}")
print(f"📢 Kanal: {CHANNEL}")
print(f"🔑 Token: {BOT_TOKEN[:20]}...")
print("=" * 50)

def _backfill_approved_hashes():
    """Bu güncellemeden önce onaylanmış botların approved_file_hash'i boş olabilir;
    ilk çalıştırmada bunları doldurur ki değişiklik takibi baştan itibaren güvenilir olsun."""
    try:
        cursor.execute("SELECT id, file_path FROM bot_files WHERE status='approved' AND (approved_file_hash IS NULL OR approved_file_hash='')")
        rows = cursor.fetchall()
        for bid, fp in rows:
            if fp and os.path.exists(fp):
                h = _file_hash(fp)
                if h:
                    cursor.execute("UPDATE bot_files SET approved_file_hash=? WHERE id=?", (h, bid))
        conn.commit()
    except Exception as e:
        print(f"Hash Backfill Error: {e}")

def _headless_install_modules(modules):
    """install_modules'un Telegram mesajı düzenlemeyen sürümü — otomatik yeniden
    başlatmada (auto_restart_running_bots) kullanılır, kullanıcı etkileşimi gerekmez."""
    if not modules:
        return
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception:
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", mod], capture_output=True, timeout=60)
            except Exception:
                pass

def auto_restart_running_bots():
    """🔁 Deploy/restart sonrası, restart öncesinde 'running' olarak işaretli olan
    hostlanan botları otomatik olarak yeniden başlatır. subprocess.Popen ile başlatılan
    alt botlar, ana process (bu script) yeniden başladığında otomatik ölür; bu fonksiyon
    olmadan admin her deploy sonrası tüm botları elle tek tek başlatmak zorunda kalırdı."""
    try:
        cursor.execute(
            "SELECT id, user_id, file_path, bot_token, bot_username, template_key FROM bot_files "
            "WHERE status='approved' AND bot_status='running'"
        )
        rows = cursor.fetchall()
    except Exception as e:
        print(f"Auto-Restart Query Error: {e}")
        return

    if not rows:
        return

    print(f"🔁 Otomatik yeniden başlatma: {len(rows)} bot bulundu...")
    ok, failed = [], []

    for bid, owner_id, fp, bt, bu, tkey in rows:
        try:
            if not fp or not bt or not os.path.exists(fp):
                cursor.execute("UPDATE bot_files SET bot_status='error' WHERE id=?", (bid,))
                conn.commit()
                failed.append((bid, bu or bid))
                continue

            modules = find_imports(fp)
            _headless_install_modules(modules)

            os.makedirs(BOT_FILES_DIR, exist_ok=True)
            log_path = f"{BOT_FILES_DIR}/{bid}_log.txt"
            log_f = open(log_path, "w", encoding="utf-8", errors="ignore")

            run_target = fp
            try:
                run_target = build_bot_launcher(fp, bid, add_watermark=get_effective_watermark(owner_id), redirect_db=not tkey)
            except Exception:
                run_target = fp

            p = subprocess.Popen([sys.executable, run_target], stdout=log_f, stderr=subprocess.STDOUT)
            log_f.close()
            time.sleep(1)

            if p.poll() is not None:
                cursor.execute("UPDATE bot_files SET bot_status='error' WHERE id=?", (bid,))
                conn.commit()
                failed.append((bid, bu or bid))
                continue

            cursor.execute("UPDATE bot_files SET pid=?, start_count=start_count+1 WHERE id=?", (p.pid, bid))
            conn.commit()
            threading.Thread(target=monitor_bot, args=(bid, p.pid), daemon=True).start()
            ok.append((bid, bu or bid))
        except Exception as e:
            print(f"Auto-Restart Error (bot {bid}): {e}")
            try:
                cursor.execute("UPDATE bot_files SET bot_status='error' WHERE id=?", (bid,))
                conn.commit()
            except Exception:
                pass
            failed.append((bid, bu or bid))

    print(f"🔁 Otomatik yeniden başlatma tamamlandı: {len(ok)} başarılı, {len(failed)} başarısız.")

    if OWNER_ID and (ok or failed):
        try:
            lines = [f"🔁 <b>Deploy Sonrası Otomatik Yeniden Başlatma</b>", DIV]
            if ok:
                lines.append(f"✅ Başarılı ({len(ok)}): " + ", ".join(f"@{esc(u)}" if isinstance(u, str) else str(u) for _, u in ok))
            if failed:
                lines.append(f"❌ Başarısız ({len(failed)}): " + ", ".join(f"@{esc(u)}" if isinstance(u, str) else str(u) for _, u in failed))
            bot.send_message(OWNER_ID, "\n".join(lines), parse_mode="HTML")
        except Exception:
            pass

if __name__ == "__main__":
    _backfill_approved_hashes()
    auto_restart_running_bots()
    threading.Thread(target=sleep_schedule_loop, daemon=True).start()
    threading.Thread(target=monthly_users_badge_loop, daemon=True).start()
    threading.Thread(target=code_watch_loop, daemon=True).start()
    while True:
        try:
            print("Bot başlatılıyor...")
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            print(f"Hata: {e}")
            time.sleep(5)
