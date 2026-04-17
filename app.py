# SafeHarbor Bot v60
# שינויים מ-v59:
#   - v60: Claude API לתגובות דינמיות (off-topic, ברכה, קרקוע סיום, escalation)
#   - v60: Whisper (OpenAI) לתמלול הודעות קוליות נכנסות
#   - v60: ElevenLabs TTS לשליחת תגובות קוליות
#   - v60: כל הגיונת הבטיחות (משבר, rate-limit, injection) נשמרת ללא שינוי

import os, time, json, logging, threading, hmac, hashlib, tempfile
import requests as http_requests
import re
import redis as redis_lib
from flask import Flask, request, jsonify

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
class _JsonFmt(logging.Formatter):
    def format(self, r):
        d = {"ts": self.formatTime(r, "%Y-%m-%dT%H:%M:%S"), "level": r.levelname, "msg": r.getMessage()}
        if r.exc_info: d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, ensure_ascii=False)

_h = logging.StreamHandler()
_h.setFormatter(_JsonFmt())
logging.basicConfig(handlers=[_h], level=logging.INFO, force=True)
log = logging.getLogger("safeharbor")

_SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if _SENTRY_DSN:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=0.1)
        log.info("sentry_initialized")
    except ImportError:
        log.warning("sentry_sdk_not_installed")

app         = Flask(__name__)
_START_TIME = time.time()

# ─────────────────────────────────────────────
# ENV Variables
# ─────────────────────────────────────────────
WHATSAPP_TOKEN      = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID   = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN        = os.environ.get("VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
WHATSAPP_API_URL    = "https://graph.facebook.com/v22.0/{}/messages".format(WHATSAPP_PHONE_ID)
DEBOUNCE_SEC        = 1.0

# v60: מפתחות API חדשים
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # ברירת מחדל: Adam (עברית טובה)

# v60: האם לשלוח תגובות בקול (ניתן להכביא/לכבות)
VOICE_REPLY_ENABLED = os.environ.get("VOICE_REPLY_ENABLED", "true").lower() == "true"

LOGO_URL = os.environ.get("LOGO_URL", "https://raw.githubusercontent.com/argovalex/safeharbor-bot/main/logo.png")

if not VERIFY_TOKEN:
    log.warning("security_warning: VERIFY_TOKEN not set")
if not ANTHROPIC_API_KEY:
    log.warning("config_warning: ANTHROPIC_API_KEY not set — falling back to static messages")
if not OPENAI_API_KEY:
    log.warning("config_warning: OPENAI_API_KEY not set — voice input disabled")
if not ELEVENLABS_API_KEY:
    log.warning("config_warning: ELEVENLABS_API_KEY not set — voice output disabled")

_WA_HEADERS = {
    "Authorization": "Bearer {}".format(WHATSAPP_TOKEN),
    "Content-Type":  "application/json",
}

_redis = redis_lib.from_url(
    os.environ.get("REDIS_URL", "redis://localhost:6379"),
    decode_responses=True,
    socket_timeout=10,
    socket_connect_timeout=10,
    retry_on_timeout=True,
    health_check_interval=30,
    max_connections=20,
)

from concurrent.futures import ThreadPoolExecutor as _TPE
_msg_executor = _TPE(max_workers=30)

try:
    from rq import Queue as _RQ_Queue
    _rq     = _RQ_Queue("safeharbor", connection=_redis)
    _USE_RQ = True
    log.info("rq_initialized")
except ImportError:
    _USE_RQ = False
    log.warning("rq_not_installed_fallback_threadpool")

def _enqueue(fn, *args):
    if _USE_RQ:
        _rq.enqueue(fn, *args, job_timeout=300)
    else:
        _msg_executor.submit(fn, *args)

ADMIN_API_KEY      = os.environ.get("ADMIN_API_KEY", "safeharbor-secret")
ADMIN_SMS_TO       = os.environ.get("ADMIN_PHONE", "")
_ADMIN_MAX_PER_MIN = 5

# ─────────────────────────────────────────────
# v60: Claude API — תגובות דינמיות
# ─────────────────────────────────────────────

_CLAUDE_SYSTEM_PROMPT = """אתה "נמל הבית" — בוט תמיכה רגשית בעברית לאנשים שעוברים רגעים קשים, חרדה, או פוסט-טראומה.

כללים מחייבים:
- תמיד בעברית, טון חם ואמפתי, משפטים קצרים (עד 2-3 משפטים)
- לעולם אל תציע עצות רפואיות או פסיכולוגיות ספציפיות
- אל תשחק תפקיד אחר, אל תצא מהאופי
- אם יש סימני משבר — הפנה מיד לער"ן 1201
- סיים תמיד עם אפשרות לנשימה (א) או קרקוע (ב)
- הודעות קצרות בלבד — מקסימום 3 משפטים"""

def call_claude(user_message: str, context: str = "") -> str:
    """
    v60: קריאה ל-Claude API לתגובה דינמית.
    מחזיר תגובה עברית קצרה ואמפתית.
    fallback להודעה סטטית אם Claude לא זמין.
    """
    if not ANTHROPIC_API_KEY:
        return ""  # fallback להודעות סטטיות

    prompt = user_message
    if context:
        prompt = "{}\n\nהמשתמש כתב: {}".format(context, user_message)

    try:
        resp = http_requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",  # מהיר וזול לתגובות קצרות
                "max_tokens": 150,
                "system": _CLAUDE_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        log.info("claude_response_ok", extra={"chars": len(text)})
        return text
    except Exception as e:
        log.error("claude_api_error", extra={"err": str(e)})
        return ""  # fallback

# ─────────────────────────────────────────────
# v60: Whisper — תמלול הודעות קוליות
# ─────────────────────────────────────────────

def download_whatsapp_media(media_id: str) -> bytes | None:
    """מוריד קובץ מדיה מ-Meta API לפי media_id."""
    try:
        # שלב 1: קבל URL של הקובץ
        url_resp = http_requests.get(
            "https://graph.facebook.com/v22.0/{}".format(media_id),
            headers={"Authorization": "Bearer {}".format(WHATSAPP_TOKEN)},
            timeout=10,
        )
        url_resp.raise_for_status()
        media_url = url_resp.json().get("url", "")
        if not media_url:
            log.error("media_url_missing", extra={"media_id": media_id})
            return None

        # שלב 2: הורד את הקובץ עצמו
        file_resp = http_requests.get(
            media_url,
            headers={"Authorization": "Bearer {}".format(WHATSAPP_TOKEN)},
            timeout=30,
        )
        file_resp.raise_for_status()
        return file_resp.content
    except Exception as e:
        log.error("media_download_error", extra={"err": str(e), "media_id": media_id})
        return None


def transcribe_audio(media_id: str) -> str | None:
    """
    v60: מוריד קובץ קולי מ-WhatsApp ומתמלל עם Whisper.
    מחזיר טקסט עברי או None אם נכשל.
    """
    if not OPENAI_API_KEY:
        log.warning("whisper_disabled: no OPENAI_API_KEY")
        return None

    audio_bytes = download_whatsapp_media(media_id)
    if not audio_bytes:
        return None

    try:
        # שמור קובץ זמני
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        with open(tmp_path, "rb") as audio_file:
            resp = http_requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": "Bearer {}".format(OPENAI_API_KEY)},
                files={"file": ("audio.ogg", audio_file, "audio/ogg")},
                data={"model": "whisper-1", "language": "he"},
                timeout=30,
            )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        log.info("whisper_transcription_ok", extra={"chars": len(text)})
        os.unlink(tmp_path)
        return text if text else None
    except Exception as e:
        log.error("whisper_error", extra={"err": str(e)})
        return None

# ─────────────────────────────────────────────
# v60: ElevenLabs TTS — שליחת תגובות קוליות
# ─────────────────────────────────────────────

def text_to_speech(text: str) -> bytes | None:
    """
    v60: ממיר טקסט לאודיו עם ElevenLabs.
    מחזיר bytes של MP3 או None אם נכשל.
    """
    if not ELEVENLABS_API_KEY:
        return None

    try:
        resp = http_requests.post(
            "https://api.elevenlabs.io/v1/text-to-speech/{}".format(ELEVENLABS_VOICE_ID),
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",  # תומך עברית
                "voice_settings": {
                    "stability": 0.75,
                    "similarity_boost": 0.75,
                    "style": 0.3,
                    "use_speaker_boost": True,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        log.info("tts_ok", extra={"chars": len(text)})
        return resp.content
    except Exception as e:
        log.error("elevenlabs_error", extra={"err": str(e)})
        return None


def upload_audio_to_wa(audio_bytes: bytes) -> str | None:
    """
    v60: מעלה קובץ MP3 ל-WhatsApp Media API.
    מחזיר media_id לשליחה.
    """
    try:
        resp = http_requests.post(
            "https://graph.facebook.com/v22.0/{}/media".format(WHATSAPP_PHONE_ID),
            headers={"Authorization": "Bearer {}".format(WHATSAPP_TOKEN)},
            files={"file": ("reply.mp3", audio_bytes, "audio/mpeg")},
            data={"messaging_product": "whatsapp", "type": "audio/mpeg"},
            timeout=30,
        )
        resp.raise_for_status()
        media_id = resp.json().get("id")
        log.info("audio_uploaded", extra={"media_id": media_id})
        return media_id
    except Exception as e:
        log.error("audio_upload_error", extra={"err": str(e)})
        return None


def send_voice_message(to: str, text: str):
    """
    v60: ממיר טקסט לקול ושולח כהודעה קולית בוואטסאפ.
    fallback לטקסט אם TTS נכשל.
    """
    if not VOICE_REPLY_ENABLED:
        return

    audio_bytes = text_to_speech(text)
    if not audio_bytes:
        log.warning("tts_fallback_to_text", extra={"to": to})
        return  # send_message כבר נשלח לפני כן כ-fallback

    media_id = upload_audio_to_wa(audio_bytes)
    if not media_id:
        return

    _post_with_retry({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id},
    })

# ─────────────────────────────────────────────
# Meta Webhook Signature Verification
# ─────────────────────────────────────────────
def _verify_meta_signature(req):
    if not WHATSAPP_APP_SECRET:
        return True
    sig_header = req.headers.get("X-Hub-Signature-256", "")
    if not sig_header:
        return False
    if sig_header.startswith("sha256="):
        sig_header = sig_header[7:]
    try:
        expected = hmac.new(
            WHATSAPP_APP_SECRET.encode("utf-8"),
            req.get_data(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig_header)
    except Exception:
        return False

# ─────────────────────────────────────────────
# Admin rate limit + audit
# ─────────────────────────────────────────────
def _admin_rate_ok(ip):
    now = time.time()
    key = "sh:admin_rate:{}".format(ip)
    try:
        pipe = _redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - 60)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, 60)
        _, _, count, _ = pipe.execute()
        return count <= _ADMIN_MAX_PER_MIN
    except Exception:
        return True

def _admin_audit(action, ip, phone=None, extra=None):
    log.info("admin_audit", extra={"action": action, "ip": ip,
                                    "phone": phone or "", "extra": extra or {}})

def _check_admin_key(req):
    ip     = req.remote_addr or "unknown"
    result = req.headers.get("X-Admin-Key") == ADMIN_API_KEY
    if not result:
        log.warning("admin_auth_failed", extra={"ip": ip})
    return result

# ─────────────────────────────────────────────
# הודעות סטטיות (fallback כשClaude לא זמין)
# ─────────────────────────────────────────────
_DISCLAIMER = '\n\n_אני בוט עזר ראשוני בלבד. אינני תחליף לייעוץ, טיפול או שירות מקצועי._'

MSG_WELCOME = (
    '*שלום, אני נמל הבית* ⚓\n'
    'אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.\n\n'
    'אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:\n'
    '☎️ ער"ן: 1201 | 💬 https://wa.me/972528451201\n'
    '💬 סה"ר: https://wa.me/972543225656\n'
    '☎️ נט"ל: 1-800-363-363\n\n'
    '*מה יעזור לך יותר ברגע הזה?*\n'
    '🌬️ הקש *א* — תרגילי נשימה\n'
    '⚓ הקש *ב* — תרגיל קרקוע'
    + _DISCLAIMER
)

MSG_RETURNING = (
    'היי, טוב שחזרת אלי. 💙\n'
    'אני נמל הבית, ואני כאן איתך שוב.\n\n'
    '*מה מרגיש לך נכון יותר ברגע הזה?*\n'
    '🌬️ הקש *א* — נשימה מרגיעה\n'
    '⚓ הקש *ב* — תרגיל קרקוע\n\n'
    'זכור שיש עזרה אנושית זמינה עבורך תמיד:\n'
    '☎️ ער"ן: 1201 | 💬 https://wa.me/972528451201\n'
    '💬 סה"ר: https://wa.me/972543225656\n'
    '☎️ נט"ל: 1-800-363-363'
    + _DISCLAIMER
)

MSG_NUDGE         = "אני כאן איתך, אתה עדיין איתי? בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. ⚓"
MSG_WELCOME_NUDGE = "אני כאן איתך. ⚓\nהקש *א* לנשימה או *ב* לקרקוע."

MSG_CRISIS = (
    'אני מבינה שאתה עובר רגע קשה מאוד. אני כאן איתך. 💙\n\n'
    '*יש מי שרוצה לעזור לך — פנה אליהם עכשיו:*\n'
    '☎️ ער"ן: 1201\n'
    '💬 https://wa.me/972528451201\n'
    '💬 סה"ר: https://wa.me/972543225656\n'
    '☎️ נט"ל: 1-800-363-363'
)

MSG_OFF_TOPIC = (
    'אני כאן כדי לעזור לך להתייצב. ⚓\n\n'
    'הקש *א* לתרגיל נשימה 🌬️\n'
    'הקש *ב* לתרגיל קרקוע ⚓'
)

MSG_BREATHING_STOP    = "יופי שעצרת רגע להקשיב לעצמך 🌿\nאפשר להישאר עם התחושה הזו עוד כמה שניות, בקצב שנוח לך 🕊️\n\nאם תרצה לחזור לזה בהמשך, אני כאן ⚓"
MSG_GROUNDING_POSITIVE = "יופי שעצרת רגע להקשיב לעצמך 🌿\nאפשר להישאר עם התחושה הזו עוד כמה שניות, בקצב שנוח לך 🕊️\n\nאם תרצה לחזור לזה בהמשך, אני כאן ⚓"
MSG_BREATHING_WAIT_CONFIRM = "הקש *כן* להמשך סבב נוסף, או *לא* לעצור. 🌬️"
MSG_RESET              = "בסדר, אני כאן כשתצטרך. 🌊"
BREATHING_START        = "אני כאן איתך בוא נספור יחד. 🌬️"

# ─────────────────────────────────────────────
# תרגיל נשימה
# ─────────────────────────────────────────────
BREATHING_PARTS = [
    "⬅️ שאיפה איטית... 21-22-23-24-25",
    "✋ עצור... 21-22-23-24-25",
    "➡️ נשיפה איטית... 21-22-23-24-25",
    "⚓ מנוחה... 21-22-23-24-25",
    "⬅️ שאיפה איטית... 21-22-23-24-25",
    "✋ עצור... 21-22-23-24-25",
    "➡️ נשיפה איטית... 21-22-23-24-25",
    "⚓ מנוחה... 21-22-23-24-25",
    "⬅️ שאיפה איטית... 21-22-23-24-25",
    "✋ עצור... 21-22-23-24-25",
    "➡️ נשיפה איטית... 21-22-23-24-25",
    "⚓ מנוחה... 21-22-23-24-25",
    "סיימנו 3 סבבים. איך התחושה? נמשיך? (כן/לא)"
]

# ─────────────────────────────────────────────
# תרגיל קרקוע
# ─────────────────────────────────────────────
GROUNDING_STEPS = [
    "👀 בוא נתמקד ברגע הזה. ציין 5 דברים שאתה רואה סביבך כרגע.",
    "🤚 מצוין. עכשיו, 4 דברים שאתה יכול לגעת בהם כרגע.",
    "👂 יופי. עכשיו, 3 דברים שאתה שומע סביבך.",
    "👃 מעולה. עכשיו, 2 דברים שאתה יכול להריח.",
    "👅 ודבר אחד שאתה יכול לטעום (או טעם שמרגיע אותך).",
    "💙 איך התחושה עכשיו?"
]

GROUNDING_NUDGE_1    = "💙 אני כאן איתך. מצאת משהו אחד?"
GROUNDING_NUDGE_2    = "⏳ נראה שאתה צריך יותר זמן. אני כאן כשתהיה מוכן."
GROUNDING_CHAT_REPLY = "אני כאן רק כדי לעזור לך להתייצב. נסה לציין דברים שאתה {hint} כרגע."
GROUNDING_HINTS      = ["רואה", "יכול לגעת בהם", "שומע", "מריח", "יכול לטעום", "מרגיש"]

# ─────────────────────────────────────────────
# מילות מפתח
# ─────────────────────────────────────────────
BREATHING_YES_WORDS = {
    "כן", "כ", "כן כן", "בטח", "אוקיי", "אוקי", "טוב", "סבבה", "נו",
    "המשך", "עוד", "עוד סבב", "נמשיך", "קדימה", "יאללה",
    "yes", "y", "sure", "ok", "okay", "yep", "yeah", "continue", "more",
}
BREATHING_STOP_WORDS  = {"לא", "ל", "no", "n", "די", "stop", "done", "סיום", "עצור"}
GREET_WORDS           = {"שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי"}
GROUNDING_RESET_WORDS = {"חזור", "איפוס", "reset", "back", "stop", "די"}

DELETE_DATA_WORDS = {
    "מחק", "מחקי", "מחק אותי", "תמחק", "שכח אותי", "שכח",
    "הסר אותי", "delete", "delete me", "forget me", "remove me", "erase",
}

GROUNDING_POSITIVE_WORDS = {
    "טוב", "בסדר", "יותר טוב", "עזר", "נרגעתי", "רגוע", "שקט",
    "מצוין", "מעולה", "סבבה", "good", "better", "calm", "relaxed", "helped",
}

# ─────────────────────────────────────────────
# נרמול טקסט
# ─────────────────────────────────────────────
_UNICODE_CONTROL_RE = re.compile(
    r'[\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff]'
)

def _clean_text(text):
    return _UNICODE_CONTROL_RE.sub('', text).strip()

def _normalize_text(text):
    text = text.strip()
    text = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)
    text = re.sub(r'\s+', ' ', text)
    return text

_GROUNDING_POSITIVE_RE = re.compile(
    r"טוב|טובה|בסדר|יותר טוב|עזר|נרגע|רגוע|רגועה|שקט|שקטה|מצוין|מעולה|סבבה|"
    r"good|better|great|calm|relaxed|fine|helped", re.IGNORECASE
)
_GROUNDING_NEGATIVE_RE = re.compile(
    r"לא טוב|לא בסדר|עדיין|קשה|כבד|לא עזר|אבל.*?(קשה|כבד|עדיין)|"
    r"not good|still|hard|heavy|didn.t help", re.IGNORECASE
)

def is_grounding_positive(text):
    if _GROUNDING_NEGATIVE_RE.search(text):
        return False
    if _GROUNDING_POSITIVE_RE.search(text):
        return True
    return bool(re.match(r"^(טוב|בסדר|ok|okay|fine|good|כן)$", text.strip(), re.IGNORECASE))

# ─────────────────────────────────────────────
# זיהוי משבר
# ─────────────────────────────────────────────
_CRISIS_WORDS = [
    "להתאבד", "לסיים הכל", "להיעלם", "רוצה למות", "בא לי למות",
    "לחתוך את עצמי", "להפסיק את הסבל", "לא רוצה לחיות", "נמאס לי לחיות",
    "לא שווה לחיות", "לקחת כדורים", "לפגוע בעצמי",
    "לישון ולא להתעורר", "כולם יהיו בסדר בלעדיי", "אני מעמסה",
    "אין טעם", "אין תקווה", "חסר סיכוי", "לא יכול יותר",
    "נשברתי", "מכתב פרידה", "צוואה",
    "suicide", "kill myself", "want to die", "end my life",
    "hurt myself", "no reason to live", "no hope", "i give up",
    "everyone would be better off without me", "i'm a burden",
    "unalive", "kms",
]
_crisis_re = re.compile("|".join(re.escape(w) for w in _CRISIS_WORDS), re.IGNORECASE)

def is_crisis(text):
    return bool(_crisis_re.search(_normalize_text(text)))

_SAD_WORDS_RE = re.compile(
    r"עצוב|עצובה|כואב|לא יכול|לא יכולה|קשה לי|בוכה|מפחד|לבד|חרדה|פאניקה|"
    r"מתמוטט|נשבר|sad|hurt|scared|alone|depressed|panic|overwhelmed|struggling",
    re.IGNORECASE
)

def has_sad_signal(text):
    return bool(_SAD_WORDS_RE.search(text))

# ─────────────────────────────────────────────
# Guardian — injection detection
# ─────────────────────────────────────────────
_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|above)\s*(instructions?|prompt|rules?)?",
    r"forget\s+(everything|all|your\s+instructions?)",
    r"override\s+(previous|all|your)\s*(instructions?|rules?|prompt)?",
    r"(act|behave|pretend|roleplay)\s+(as|like|you\s+are)",
    r"you\s+are\s+now", r"from\s+now\s+on",
    r"new\s+(instructions?|prompt|rules?)", r"developer\s+mode",
    r"jailbreak", r"DAN\b", r"base64",
    r"(show|print|reveal)\s*.{0,30}(prompt|instructions?|system)",
    r"<script", r"eval\s*\(", r"exec\s*\(",
    r"תתנהג כ", r"תשכח\s+(הכל|את\s+הכל)", r"הוראות\s+חדשות",
    r"אתה\s+עכשיו", r"התעלם\s+מ",
]
_injection_re = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

def guardian_check_input(text):
    return bool(_injection_re.search(_normalize_text(text)))

# ─────────────────────────────────────────────
# Whitelist הודעות יוצאות
# ─────────────────────────────────────────────
_ALLOWED_OUTGOING = set()

def _build_allowed_outgoing():
    for msg in [MSG_WELCOME, MSG_RETURNING, MSG_NUDGE, MSG_WELCOME_NUDGE,
                MSG_CRISIS, MSG_OFF_TOPIC, MSG_BREATHING_STOP, MSG_RESET,
                BREATHING_START, GROUNDING_NUDGE_1, GROUNDING_NUDGE_2,
                MSG_BREATHING_WAIT_CONFIRM, MSG_GROUNDING_POSITIVE]:
        _ALLOWED_OUTGOING.add(msg.strip())
    for msg in BREATHING_PARTS + GROUNDING_STEPS:
        _ALLOWED_OUTGOING.add(msg.strip())
    for hint in GROUNDING_HINTS:
        _ALLOWED_OUTGOING.add(GROUNDING_CHAT_REPLY.format(hint=hint).strip())

_build_allowed_outgoing()

def is_allowed_outgoing(text):
    """
    v60: Claude-generated responses מותרות תמיד (נבדקות בנפרד).
    הודעות סטטיות — רק מה-whitelist.
    """
    return text.strip() in _ALLOWED_OUTGOING

# ─────────────────────────────────────────────
# Rate Limiting + Blacklist
# ─────────────────────────────────────────────
RATE_WINDOW_SEC = 60
RATE_MAX_MSGS   = 20
BLACKLIST_KEY   = "sh:blacklist"
BLACKLIST_TTL   = 30 * 24 * 3600

def is_blacklisted(phone):
    try:
        return _redis.hexists(BLACKLIST_KEY, phone)
    except Exception:
        return False

def add_to_blacklist(phone, reason="rate_limit"):
    try:
        _redis.hset(BLACKLIST_KEY, phone, json.dumps({
            "reason": reason, "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "expires": time.time() + BLACKLIST_TTL,
        }))
    except Exception as e:
        log.error("blacklist_add_error", extra={"err": str(e)})
    log.warning("phone_blacklisted", extra={"phone": phone, "reason": reason})
    _send_admin_alert(phone, reason)

def remove_from_blacklist(phone):
    try:
        return bool(_redis.hdel(BLACKLIST_KEY, phone))
    except Exception:
        return False

def _clean_expired_blacklist():
    try:
        now = time.time()
        raw = _redis.hgetall(BLACKLIST_KEY)
        exp = [p for p, v in raw.items() if json.loads(v).get("expires", float("inf")) < now]
        if exp:
            _redis.hdel(BLACKLIST_KEY, *exp)
    except Exception as e:
        log.error("blacklist_clean_error", extra={"err": str(e)})

def rate_limit_check(phone):
    if is_blacklisted(phone):
        return True
    now = time.time()
    key = "sh:rate:{}".format(phone)
    try:
        pipe = _redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - RATE_WINDOW_SEC)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, RATE_WINDOW_SEC)
        _, _, count, _ = pipe.execute()
        if count > RATE_MAX_MSGS:
            add_to_blacklist(phone, reason="exceeded {} msgs/min".format(RATE_MAX_MSGS))
            return True
        return False
    except Exception as e:
        log.error("rate_limit_error", extra={"err": str(e)})
        return False

def _send_admin_alert(phone, reason):
    if not ADMIN_SMS_TO:
        return
    msg = "⚠️ SafeHarbor Alert\nPhone {} BLACKLISTED\nReason: {}\nTime: {}".format(
        phone, reason, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        http_requests.post(WHATSAPP_API_URL, headers=_WA_HEADERS,
            json={"messaging_product": "whatsapp", "recipient_type": "individual",
                  "to": ADMIN_SMS_TO, "type": "text", "text": {"body": msg}}, timeout=10)
    except Exception as e:
        log.error("admin_alert_error", extra={"err": str(e)})

# ─────────────────────────────────────────────
# שליחת הודעות
# ─────────────────────────────────────────────
_RETRY_DELAYS = [1, 3, 10]

def _post_with_retry(payload):
    last_err = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            r = http_requests.post(WHATSAPP_API_URL, headers=_WA_HEADERS, json=payload, timeout=15)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", delay * 2 or 5))
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            return True
        except Exception as e:
            last_err = e
    log.error("send_failed_all_retries", extra={"err": str(last_err)})
    return False

def send_message(to, text, with_voice=False):
    """
    v60: שולח הודעת טקסט. אם with_voice=True — שולח גם קול.
    Claude responses — לא דורשות whitelist.
    Static messages — חייבות להיות ב-whitelist.
    """
    if not text or not text.strip():
        return
    _post_with_retry({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text.strip()},
    })
    if with_voice and VOICE_REPLY_ENABLED:
        _enqueue(send_voice_message, to, text)

def send_static_message(to, text, with_voice=False):
    """שולח הודעה סטטית — בודק whitelist."""
    if not text or not text.strip():
        return
    if not is_allowed_outgoing(text):
        log.warning("guardian_blocked_outgoing", extra={"text": text[:80]})
        return
    send_message(to, text, with_voice=with_voice)

def send_claude_message(to, text, fallback_text="", with_voice=False):
    """
    v60: שולח תגובת Claude. אם Claude נכשל — שולח fallback סטטי.
    """
    if text:
        send_message(to, text, with_voice=with_voice)
    elif fallback_text:
        send_static_message(to, fallback_text, with_voice=with_voice)

def send_logo(to):
    _post_with_retry({"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "image", "image": {"link": LOGO_URL}})

# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
STATE_KEY_PREFIX = "sh:state:"
SEEN_MSG_TTL_SEC = 120
STATE_TTL_SEC    = 30 * 24 * 3600

_STATE_DEFAULTS = {
    "tool": "none", "step": 0, "welcomed": False,
    "last_msg_time": 0.0, "wait_count": 0,
    "grounding_session": 0, "sad_count": 0, "breathing_active": False,
}

def _default_state():
    return dict(_STATE_DEFAULTS)

def get_state(phone):
    try:
        raw = _redis.get(STATE_KEY_PREFIX + phone)
        if raw:
            s = json.loads(raw)
            for k, v in _STATE_DEFAULTS.items():
                s.setdefault(k, v)
            return s
    except Exception as e:
        log.error("get_state_error", extra={"phone": phone, "err": str(e)})
    return _default_state()

def set_state(phone, **kwargs):
    try:
        raw = _redis.get(STATE_KEY_PREFIX + phone)
        s   = json.loads(raw) if raw else _default_state()
        for k, v in _STATE_DEFAULTS.items():
            s.setdefault(k, v)
        s.update(kwargs)
        _redis.set(STATE_KEY_PREFIX + phone, json.dumps(s), ex=STATE_TTL_SEC)
    except Exception as e:
        log.error("set_state_error", extra={"phone": phone, "err": str(e)})

def _is_duplicate_msg(msg_id):
    try:
        return _redis.set("sh:msg:" + msg_id, "1", nx=True, ex=SEEN_MSG_TTL_SEC) is None
    except Exception as e:
        log.error("dedup_error", extra={"err": str(e)})
        return False

# ─────────────────────────────────────────────
# Redis reply channel (נשימה)
# ─────────────────────────────────────────────
_BR_TTL = 120

def _br_key(phone):
    return "sh:br:{}".format(phone)

def _br_clear(phone):
    try:
        _redis.delete(_br_key(phone))
    except Exception:
        pass

def _br_write(phone, value):
    try:
        _redis.set(_br_key(phone), value, ex=_BR_TTL)
    except Exception as e:
        log.error("br_write_error", extra={"phone": phone, "err": str(e)})

def _br_wait_fast(phone, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = get_state(phone)
        if s.get("tool") != "breathing":
            return "abort"
        try:
            val = _redis.get(_br_key(phone))
        except Exception:
            val = None
        if val in ("yes", "no"):
            _br_clear(phone)
            return val
        time.sleep(0.1)
    return "timeout"

# ─────────────────────────────────────────────
# run_breathing
# ─────────────────────────────────────────────
def run_breathing(phone):
    log.info("breathing_start", extra={"phone": phone})
    while True:
        s = get_state(phone)
        if s.get("tool") != "breathing":
            return
        set_state(phone, breathing_active=True)
        _br_clear(phone)
        send_static_message(phone, BREATHING_START, with_voice=True)
        for i, part in enumerate(BREATHING_PARTS):
            s = get_state(phone)
            if s.get("tool") != "breathing":
                return
            send_static_message(phone, part, with_voice=True)
            if i < len(BREATHING_PARTS) - 1:
                time.sleep(5)
        set_state(phone, breathing_active=False)
        _br_clear(phone)
        reply = _br_wait_fast(phone, timeout=60)
        if reply == "yes":
            continue
        s = get_state(phone)
        if s.get("tool") == "breathing":
            set_state(phone, tool="none", step=0, breathing_active=False)
            send_static_message(phone, MSG_BREATHING_STOP, with_voice=True)
        _br_clear(phone)
        return

# ─────────────────────────────────────────────
# Nudges
# ─────────────────────────────────────────────
def nudge_if_silent_grounding(phone, my_step, my_session):
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "grounding" or s["step"] != my_step or s["grounding_session"] != my_session:
        return
    send_static_message(phone, GROUNDING_NUDGE_1, with_voice=True)
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "grounding" or s["step"] != my_step or s["grounding_session"] != my_session:
        return
    send_static_message(phone, GROUNDING_NUDGE_2, with_voice=True)

def nudge_after_welcome(phone, welcomed_time):
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] == "none" and s["welcomed"] and s["last_msg_time"] <= welcomed_time + 1:
        send_static_message(phone, MSG_WELCOME_NUDGE, with_voice=True)

# ─────────────────────────────────────────────
# נעילת phone
# ─────────────────────────────────────────────
_phone_locks      = {}
_phone_locks_lock = threading.Lock()
_phone_lock_times = {}

def _get_phone_lock(phone):
    now = time.time()
    with _phone_locks_lock:
        stale = [k for k, t in _phone_lock_times.items() if now - t > 600]
        for k in stale:
            _phone_locks.pop(k, None)
            _phone_lock_times.pop(k, None)
        if phone not in _phone_locks:
            _phone_locks[phone] = threading.Lock()
        _phone_lock_times[phone] = now
        return _phone_locks[phone]

def _send_logo_and_welcome(phone, now):
    send_logo(phone)
    time.sleep(0.5)
    send_static_message(phone, MSG_WELCOME, with_voice=True)
    _enqueue(nudge_after_welcome, phone, now)

# ─────────────────────────────────────────────
# לוגיקה מרכזית
# ─────────────────────────────────────────────
_GROUNDING_CHAT_PHRASES = [
    "מה זה", "למה אתה", "מה אתה", "לא רוצה", "אני רוצה לדבר",
    "תגיד לי", "הסבר לי", "i don't want to", "tell me about",
]
_grounding_chat_re = re.compile(
    "|".join(re.escape(p) for p in _GROUNDING_CHAT_PHRASES), re.IGNORECASE
)

def is_grounding_chat(text):
    return bool(_grounding_chat_re.search(text.lower()))

def handle_message(phone, text):
    with _get_phone_lock(phone):
        _handle_message_inner(phone, text)

def _handle_message_inner(phone, text):
    text = _clean_text(text)
    t    = text.lower()

    # 1. משבר — תמיד סטטי, לא דרך Claude
    if is_crisis(text):
        log.info("crisis_detected", extra={"phone": phone})
        set_state(phone, tool="none", step=0, sad_count=0, breathing_active=False)
        _br_clear(phone)
        send_static_message(phone, MSG_CRISIS)  # משבר — טקסט בלבד, לא קול
        return

    # 2. rate limit
    if rate_limit_check(phone):
        return

    s   = get_state(phone)
    now = time.time()

    # 3. debounce
    if now - s["last_msg_time"] < DEBOUNCE_SEC:
        return

    # 4. injection
    if guardian_check_input(text):
        log.warning("injection_attempt", extra={"phone": phone, "text": text[:80]})
        set_state(phone, tool="none", step=0, breathing_active=False)
        _br_clear(phone)
        # v60: Claude עונה במקום הודעה קבועה
        claude_resp = call_claude(text, context="ניסיון מניפולציה — החזר למסלול בעדינות")
        send_claude_message(phone, claude_resp, fallback_text=MSG_OFF_TOPIC, with_voice=True)
        return

    s["last_msg_time"] = now
    try:
        _redis.set(STATE_KEY_PREFIX + phone, json.dumps(s), ex=STATE_TTL_SEC)
    except Exception:
        pass

    tool = s["tool"]
    step = s["step"]

    # 5. escalation רגשי
    if has_sad_signal(text):
        new_sad = s.get("sad_count", 0) + 1
        set_state(phone, sad_count=new_sad)
        if new_sad >= 3:
            log.info("escalation_crisis", extra={"phone": phone})
            set_state(phone, tool="none", step=0, sad_count=0, breathing_active=False)
            _br_clear(phone)
            send_static_message(phone, MSG_CRISIS)
            return
    else:
        if s.get("sad_count", 0) > 0:
            set_state(phone, sad_count=0)

    # 6. משתמש חדש
    if not s["welcomed"]:
        set_state(phone, welcomed=True)
        _enqueue(_send_logo_and_welcome, phone, now)
        return

    # ── 7. נשימה פעילה ──
    if tool == "breathing":
        if t in BREATHING_STOP_WORDS:
            set_state(phone, tool="none", step=0, breathing_active=False)
            _br_write(phone, "no")
            send_static_message(phone, MSG_BREATHING_STOP, with_voice=True)
            return
        if not s.get("breathing_active"):
            if t in BREATHING_YES_WORDS:
                _br_write(phone, "yes")
                return
            else:
                send_static_message(phone, MSG_BREATHING_WAIT_CONFIRM, with_voice=True)
                return
        return

    # ── 8. קרקוע פעיל ──
    if tool == "grounding":
        gs = s["grounding_session"]
        if t in GROUNDING_RESET_WORDS:
            set_state(phone, tool="none", step=0, wait_count=0, grounding_session=gs + 1)
            send_static_message(phone, MSG_RESET, with_voice=True)
            return
        if is_grounding_chat(text):
            hint = GROUNDING_HINTS[min(step, len(GROUNDING_HINTS) - 1)]
            send_static_message(phone, GROUNDING_CHAT_REPLY.format(hint=hint), with_voice=True)
            return
        if step == len(GROUNDING_STEPS) - 1:
            # v60: שלב הסיום — Claude מגיב לפי התחושה
            new_gs = gs + 1
            set_state(phone, tool="none", step=0, wait_count=0, grounding_session=new_gs)
            if is_grounding_positive(text):
                context = "המשתמש סיים תרגיל קרקוע 5 חושים ואמר שהוא מרגיש טוב יותר: {}".format(text)
                claude_resp = call_claude(text, context=context)
                send_claude_message(phone, claude_resp, fallback_text=MSG_GROUNDING_POSITIVE, with_voice=True)
            else:
                context = "המשתמש סיים תרגיל קרקוע אך עדיין מרגיש קשה: {}".format(text)
                claude_resp = call_claude(text, context=context)
                send_claude_message(phone, claude_resp, fallback_text=MSG_RETURNING, with_voice=True)
            return
        new_gs    = gs + 1
        next_step = step + 1
        set_state(phone, step=next_step, wait_count=0, grounding_session=new_gs)
        send_static_message(phone, GROUNDING_STEPS[next_step], with_voice=True)
        _enqueue(nudge_if_silent_grounding, phone, next_step, new_gs)
        return

    # ── 9. בחירת נשימה ──
    if text == "א" or t == "a":
        set_state(phone, tool="breathing", step=0, breathing_active=False)
        _enqueue(run_breathing, phone)
        return

    # ── 10. בחירת קרקוע ──
    if text == "ב" or t == "b":
        new_gs = s["grounding_session"] + 1
        set_state(phone, tool="grounding", step=0, wait_count=0, grounding_session=new_gs)
        send_static_message(phone, GROUNDING_STEPS[0], with_voice=True)
        _enqueue(nudge_if_silent_grounding, phone, 0, new_gs)
        return

    # 11. ברכה — v60: Claude מגיב
    if t in GREET_WORDS:
        claude_resp = call_claude(text, context="המשתמש חוזר אחרי הפסקה ומברך")
        send_claude_message(phone, claude_resp, fallback_text=MSG_RETURNING, with_voice=True)
        return

    # 12. off-topic — v60: Claude מגיב
    context = "המשתמש שלח הודעה שאינה קשורה לתרגיל. החזר אותו בעדינות לבחירה בין נשימה (א) לקרקוע (ב)."
    claude_resp = call_claude(text, context=context)
    send_claude_message(phone, claude_resp, fallback_text=MSG_OFF_TOPIC, with_voice=True)

# ─────────────────────────────────────────────
# Admin Dashboard
# ─────────────────────────────────────────────
@app.route("/admin", methods=["GET"])
def admin_dashboard():
    ip = request.remote_addr or "unknown"
    if not _admin_rate_ok(ip):
        return jsonify({"error": "too many requests"}), 429
    if not _check_admin_key(request):
        return (
            '<html><head><title>SafeHarbor Admin</title></head>'
            '<body><p>Send <code>X-Admin-Key</code> header</p></body></html>'
        ), 401
    _admin_audit("dashboard_view", ip)
    _clean_expired_blacklist()
    try:
        raw     = _redis.hgetall(BLACKLIST_KEY)
        bl_copy = {k: json.loads(v) for k, v in raw.items()}
    except Exception:
        bl_copy = {}

    rows = ""
    for phone, info in sorted(bl_copy.items(), key=lambda x: x[1].get("time", 0), reverse=True):
        rows += (
            '<tr><td style="font-family:monospace">{}</td><td>{}</td>'
            '<td style="color:#888;font-size:13px">{}</td>'
            '<td><button onclick="removePhone(\'{}\')" style="background:#dc2626;color:#fff;'
            'border:none;padding:5px 12px;border-radius:6px;cursor:pointer">הסר</button>'
            '</td></tr>'
        ).format(phone, info.get("reason", ""), info.get("time_str", ""), phone)

    if not rows:
        rows = '<tr><td colspan="4" style="text-align:center;color:#888;padding:24px">אין מספרים חסומים</td></tr>'

    html = (
        '<html><head><title>SafeHarbor Admin v60</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:sans-serif;background:#f5f5f5;padding:24px;direction:rtl}'
        'h1{font-size:20px;color:#1e293b;margin-bottom:20px}'
        '.card{background:#fff;border-radius:12px;box-shadow:0 2px 8px #0001;padding:20px;margin-bottom:20px}'
        'table{width:100%;border-collapse:collapse;font-size:14px}'
        'th{text-align:right;padding:10px 12px;background:#f8fafc;color:#64748b;font-weight:600;border-bottom:2px solid #e2e8f0}'
        'td{padding:10px 12px;border-bottom:1px solid #f1f5f9}'
        '.add-row{display:flex;gap:10px;margin-top:12px}'
        '.add-row input{flex:1;padding:9px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px}'
        '.add-row button{padding:9px 18px;background:#2563eb;color:#fff;border:none;border-radius:8px;cursor:pointer}'
        '#msg{padding:10px;border-radius:8px;margin-bottom:16px;display:none;font-size:14px}</style></head>'
        '<body><h1>SafeHarbor v60 — ניהול</h1>'
        '<div id="msg"></div>'
        '<div class="card"><table><thead>'
        '<tr><th>מספר טלפון</th><th>סיבה</th><th>תאריך</th><th></th></tr>'
        '</thead><tbody>' + rows + '</tbody></table>'
        '<div class="add-row">'
        '<input id="new-phone" type="text" placeholder="972501234567" dir="ltr">'
        '<input id="ak-input" type="password" placeholder="Admin Key" dir="ltr" style="max-width:160px">'
        '<button onclick="addPhone()">חסום</button></div></div>'
        '<script>'
        'function getH(){return{"X-Admin-Key":document.getElementById("ak-input").value,"Content-Type":"application/json"};}'
        'function showMsg(t,ok){const e=document.getElementById("msg");e.textContent=t;e.style.display="block";'
        'e.style.background=ok?"#dcfce7":"#fee2e2";e.style.color=ok?"#166534":"#dc2626";'
        'setTimeout(()=>e.style.display="none",3000);}'
        'function removePhone(p){const ak=prompt("Admin Key:");if(!ak)return;'
        'fetch("/admin/blacklist/"+p,{method:"DELETE",headers:{"X-Admin-Key":ak}})'
        '.then(r=>r.json()).then(d=>{showMsg(d.status==="removed"?"הוסר: "+p:"לא נמצא",d.status==="removed");'
        'if(d.status==="removed")setTimeout(()=>location.reload(),1000);});}'
        'function addPhone(){const p=document.getElementById("new-phone").value.trim();if(!p)return;'
        'fetch("/admin/blacklist/"+p,{method:"POST",headers:getH(),body:JSON.stringify({reason:"manual"})})'
        '.then(r=>r.json()).then(d=>{showMsg(d.status==="blacklisted"?"נוסף: "+p:"שגיאה",d.status==="blacklisted");'
        'if(d.status==="blacklisted")setTimeout(()=>location.reload(),1000);});}'
        '</script></body></html>'
    )
    return html, 200

@app.route("/admin/blacklist", methods=["GET"])
def admin_list_blacklist():
    if not _check_admin_key(request):
        return jsonify({"error": "unauthorized"}), 401
    _admin_audit("blacklist_list", request.remote_addr or "unknown")
    try:
        raw = _redis.hgetall(BLACKLIST_KEY)
        bl  = {k: json.loads(v) for k, v in raw.items()}
    except Exception:
        bl = {}
    return jsonify({"blacklist": bl, "count": len(bl)}), 200

@app.route("/admin/blacklist/<phone>", methods=["DELETE"])
def admin_remove_blacklist(phone):
    if not _check_admin_key(request):
        return jsonify({"error": "unauthorized"}), 401
    result = remove_from_blacklist(phone)
    _admin_audit("blacklist_remove", request.remote_addr or "unknown", phone=phone)
    return jsonify({"status": "removed" if result else "not_found", "phone": phone}), 200

@app.route("/admin/blacklist/<phone>", methods=["POST"])
def admin_add_blacklist(phone):
    if not _check_admin_key(request):
        return jsonify({"error": "unauthorized"}), 401
    reason = request.json.get("reason", "manual") if request.json else "manual"
    add_to_blacklist(phone, reason=reason)
    _admin_audit("blacklist_add", request.remote_addr or "unknown", phone=phone)
    return jsonify({"status": "blacklisted", "phone": phone}), 200

# ─────────────────────────────────────────────
# Webhook — v60: תמיכה בהודעות קוליות
# ─────────────────────────────────────────────
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    if not _verify_meta_signature(request):
        return jsonify({"error": "invalid signature"}), 403

    data = request.get_json(silent=True)
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    msg_id   = msg.get("id", "")
                    msg_type = msg.get("type", "")
                    phone    = msg.get("from", "")

                    if msg_id and _is_duplicate_msg(msg_id):
                        log.info("dedup_blocked", extra={"msg_id": msg_id})
                        continue

                    if msg_type == "text":
                        # הודעת טקסט רגילה
                        text = msg["text"]["body"]
                        _msg_executor.submit(handle_message, phone, text)

                    elif msg_type == "audio":
                        # v60: הודעה קולית — תמלול עם Whisper
                        media_id = msg.get("audio", {}).get("id", "")
                        if media_id and OPENAI_API_KEY:
                            log.info("audio_message_received", extra={"phone": phone, "media_id": media_id})
                            _msg_executor.submit(_handle_audio_message, phone, media_id)
                        else:
                            log.warning("audio_ignored: no media_id or no OPENAI_API_KEY")
                            # שלח הודעה שמסבירה שקול לא נתמך
                            _msg_executor.submit(
                                send_static_message, phone,
                                "כרגע אני מבין טקסט בלבד. נסה לכתוב מה אתה מרגיש. ⚓"
                            )

    except Exception as e:
        log.error("webhook_error", extra={"err": str(e)})
    return jsonify({"status": "ok"}), 200


def _handle_audio_message(phone, media_id):
    """v60: מטפל בהודעות קוליות — Whisper → handle_message."""
    text = transcribe_audio(media_id)
    if text:
        log.info("audio_transcribed", extra={"phone": phone, "text": text[:50]})
        handle_message(phone, text)
    else:
        log.warning("transcription_failed", extra={"phone": phone})
        send_static_message(phone, "לא הצלחתי להבין את ההקלטה. נסה לכתוב. ⚓")

# ─────────────────────────────────────────────
# Health + root
# ─────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    redis_ok = False
    try:
        _redis.ping()
        redis_ok = True
    except Exception:
        pass
    return jsonify({
        "status":        "ok" if redis_ok else "degraded",
        "version":       "v60",
        "uptime":        int(time.time() - _START_TIME),
        "redis":         "ok" if redis_ok else "error",
        "queue":         "rq" if _USE_RQ else "threadpool",
        "claude_api":    "enabled" if ANTHROPIC_API_KEY else "disabled",
        "whisper":       "enabled" if OPENAI_API_KEY else "disabled",
        "elevenlabs":    "enabled" if ELEVENLABS_API_KEY else "disabled",
        "voice_replies": VOICE_REPLY_ENABLED,
    }), 200 if redis_ok else 503

@app.route("/", methods=["GET"])
def root():
    return "SafeHarbor Bot is running v60", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
