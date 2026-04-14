# SafeHarbor Bot v56
# שינויים מ-v55:
#   1. הוסר MSG_PROFESSIONAL_REFERRAL לחלוטין
#   2. הוסר DAILY_SESSION_THRESHOLD, _increment_daily_sessions, daily_sessions, last_session_date
#   3. בחירת נשימה: set_state + _enqueue(run_breathing) בלבד
#   4. בחירת קרקוע: set_state(step=0) + send GROUNDING_STEPS[0] בלבד — תיקון ה-step 4 bug

import os, time, json, logging, threading
import requests as http_requests
import re
import redis as redis_lib
from flask import Flask, request, jsonify

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

WHATSAPP_TOKEN    = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "")
WHATSAPP_API_URL  = "https://graph.facebook.com/v22.0/{}/messages".format(WHATSAPP_PHONE_ID)
DEBOUNCE_SEC      = 1.0

if not VERIFY_TOKEN:
    log.warning("security_warning: VERIFY_TOKEN not set")

LOGO_URL = os.environ.get("LOGO_URL", "https://raw.githubusercontent.com/argovalex/safeharbor-bot/main/logo.png")

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
_ADMIN_MAX_PER_MIN = 10

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

# ─────────────────────────────────────────────
# הודעות
# ─────────────────────────────────────────────
_DISCLAIMER = '\n\n_אני בוט עזר ראשוני בלבד. אינני תחליף לייעוץ, טיפול או שירות מקצועי._'

MSG_WELCOME = (
    '*שלום, אני נמל הבית* \u2693\n'
    'אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.\n\n'
    'אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:\n'
    '\u260e\ufe0f ער"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\u260e\ufe0f נט"ל: 1-800-363-363\n\n'
    '*מה יעזור לך יותר ברגע הזה?*\n'
    '\U0001f32c\ufe0f הקש *א* — תרגילי נשימה\n'
    '\u2693 הקש *ב* — תרגיל קרקוע'
    + _DISCLAIMER
)

MSG_RETURNING = (
    'היי, טוב שחזרת אלי. \U0001f499\n'
    'אני נמל הבית, ואני כאן איתך שוב.\n\n'
    '*מה מרגיש לך נכון יותר ברגע הזה?*\n'
    '\U0001f32c\ufe0f הקש *א* — נשימה מרגיעה\n'
    '\u2693 הקש *ב* — תרגיל קרקוע\n\n'
    'זכור שיש עזרה אנושית זמינה עבורך תמיד:\n'
    '\u260e\ufe0f ער"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\u260e\ufe0f נט"ל: 1-800-363-363'
    + _DISCLAIMER
)

MSG_NUDGE         = "אני כאן איתך, אתה עדיין איתי? בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. \u2693"
MSG_WELCOME_NUDGE = "אני כאן איתך. \u2693\nהקש *א* לנשימה או *ב* לקרקוע."

MSG_CRISIS = (
    'אני מבינה שאתה עובר רגע קשה מאוד. אני כאן איתך. \U0001f499\n\n'
    '*יש מי שרוצה לעזור לך — פנה אליהם עכשיו:*\n'
    '\u260e\ufe0f ער"ן: 1201\n'
    '\U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\u260e\ufe0f נט"ל: 1-800-363-363'
)

MSG_OFF_TOPIC = (
    'אני כאן כדי לעזור לך להתייצב. \u2693\n\n'
    'הקש *א* לתרגיל נשימה \U0001f32c\ufe0f\n'
    'הקש *ב* לתרגיל קרקוע \u2693'
)

MSG_BREATHING_STOP = (
    "יופי שעצרת רגע להקשיב לעצמך \U0001f33f\n"
    "אפשר להישאר עם התחושה הזו עוד כמה שניות, בקצב שנוח לך \U0001f54a\ufe0f\n\n"
    "אם תרצה לחזור לזה בהמשך, אני כאן \u2693"
)

MSG_BREATHING_WAIT_CONFIRM = "הקש *כן* להמשך סבב נוסף, או *לא* לעצור. \U0001f32c\ufe0f"
MSG_RESET                  = "בסדר, אני כאן כשתצטרך. \U0001f30a"
BREATHING_START            = "אני כאן איתך בוא נספור יחד. \U0001f32c\ufe0f"

# ─────────────────────────────────────────────
# תרגיל נשימה — 3 מחזורי box breathing
# ─────────────────────────────────────────────
BREATHING_PARTS = [
    "\u2B05\ufe0f שאיפה איטית... 21-22-23-24-25",
    "\u270b עצור... 21-22-23-24-25",
    "\u27A1\ufe0f נשיפה איטית... 21-22-23-24-25",
    "\u2693 מנוחה... 21-22-23-24-25",
    "\u2B05\ufe0f שאיפה איטית... 21-22-23-24-25",
    "\u270b עצור... 21-22-23-24-25",
    "\u27A1\ufe0f נשיפה איטית... 21-22-23-24-25",
    "\u2693 מנוחה... 21-22-23-24-25",
    "\u2B05\ufe0f שאיפה איטית... 21-22-23-24-25",
    "\u270b עצור... 21-22-23-24-25",
    "\u27A1\ufe0f נשיפה איטית... 21-22-23-24-25",
    "\u2693 מנוחה... 21-22-23-24-25",
    "סיימנו 3 סבבים. איך התחושה? נמשיך? (כן/לא)"
]

# ─────────────────────────────────────────────
# תרגיל קרקוע
# ─────────────────────────────────────────────
GROUNDING_STEPS = [
    "\U0001f440 בוא נתמקד ברגע הזה. ציין 5 דברים שאתה רואה סביבך כרגע.",
    "\U0001f91a מצוין. עכשיו, 4 דברים שאתה יכול לגעת בהם כרגע.",
    "\U0001f442 יופי. עכשיו, 3 דברים שאתה שומע סביבך.",
    "\U0001f443 מעולה. עכשיו, 2 דברים שאתה יכול להריח.",
    "\U0001f445 ודבר אחד שאתה יכול לטעום (או טעם שמרגיע אותך).",
    "\U0001f499 איך התחושה עכשיו?"
]

GROUNDING_NUDGE_1    = "\U0001f499 אני כאן איתך. מצאת משהו אחד?"
GROUNDING_NUDGE_2    = "\u23f3 נראה שאתה צריך יותר זמן. אני כאן כשתהיה מוכן."
GROUNDING_CHAT_REPLY = "אני כאן רק כדי לעזור לך להתייצב. נסה לציין דברים שאתה {hint} כרגע."
GROUNDING_HINTS      = ["רואה", "יכול לגעת בהם", "שומע", "מריח", "יכול לטעום", "מרגיש"]

# ─────────────────────────────────────────────
# מילות מפתח
# ─────────────────────────────────────────────
BREATHING_YES_WORDS = {
    "כן", "כ", "כן כן", "בטח", "אוקיי", "אוקי", "טוב", "סבבה", "נו",
    "המשך", "עוד", "עוד סבב", "נמשיך", "קדימה", "יאללה",
    "רוצה עוד", "כן בבקשה",
    "yes", "y", "sure", "ok", "okay", "yep", "yeah", "yup",
    "continue", "more", "go", "again",
}
BREATHING_STOP_WORDS  = {"לא", "ל", "no", "n", "די", "stop", "done", "סיום", "עצור"}
GREET_WORDS           = {"שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי"}
GROUNDING_RESET_WORDS = {"חזור", "איפוס", "reset", "back", "stop", "די"}

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
    text = "".join(
        chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c
        for c in text
    )
    text = text.replace("0", "o").replace("1", "l").replace("3", "e").replace("@", "a")
    text = re.sub(r'\s+', ' ', text)
    return text

# ─────────────────────────────────────────────
# זיהוי משבר
# ─────────────────────────────────────────────
_CRISIS_WORDS = [
    "להתאבד", "לסיים הכל", "להיעלם", "רוצה למות", "בא לי למות",
    "לחתוך את עצמי", "לחתוך", "להפסיק את הסבל",
    "לא רוצה לחיות", "נמאס לי לחיות", "לא שווה לחיות",
    "לקחת כדורים", "לפגוע בעצמי",
    "לישון ולא לקום", "לישון ולא להתעורר",
    "כולם יהיו בסדר בלעדיי", "יהיה יותר טוב בלעדיי",
    "אני מעמסה על כולם", "אני מעמסה", "אני רק מכביד",
    "מי יתגעגע אלי", "אף אחד לא יתגעגע",
    "נמאס לי להיות כאן", "לא אכפת לי מה יקרה לי",
    "הייתי רוצה להיעלם", "רוצה שהכל ייפסק",
    "עייף מלחיות", "עייפה מלחיות",
    "אין טעם", "אין תקווה", "אין לי תקווה", "חסר סיכוי",
    "קצה היכולת", "לא יכול יותר", "לא יכולה יותר",
    "נמאס לי מהכל", "אבוד לי", "אבודה לי",
    "חושך מוחלט", "הכל נגמר", "אין מוצא",
    "שום דבר לא יעזור", "אין לי כוח יותר",
    "נשברתי", "נשבר לי", "מתמוטט", "מתמוטטת",
    "מכתב פרידה", "צוואה", "סליחה מכולם", "להיפרד מכולם",
    "שמרו על עצמכם", "תטפלו בעצמכם",
    "suicide", "kill myself", "want to die", "end my life",
    "cut myself", "end it all", "make it stop", "hurt myself",
    "take my life", "take pills",
    "no reason to live", "no hope", "worthless",
    "i can't do this anymore", "i give up", "there's no point",
    "no point in living", "can't go on", "tired of fighting",
    "tired of living", "tired of being alive",
    "everyone would be better off without me",
    "who would miss me", "i'm a burden", "i am a burden",
    "wish i wasn't here", "wish i was dead",
    "don't want to be here anymore",
    "unalive myself", "unalive", "kms", "kys",
    "end the pain", "no way out",
    "אין לי דרך חזרה", "אין דרך חזרה",
]
_crisis_re = re.compile("|".join(re.escape(w) for w in _CRISIS_WORDS), re.IGNORECASE)

def is_crisis(text):
    return bool(_crisis_re.search(_normalize_text(text)))

_SAD_WORDS_RE = re.compile(
    r"עצוב|עצובה|כואב|כואבת|לא יכול|לא יכולה|קשה לי|בוכה|מפחד|מפחדת|"
    r"לבד|לבדי|אין לי|אף אחד|דיכאון|חרדה|פחד|בהלה|פאניקה|"
    r"לא בסדר|מתמוטט|מתמוטטת|נשבר|נשברת|"
    r"sad|hurts|hurt|scared|alone|lonely|depressed|anxious|panic|"
    r"can.t cope|breaking down|overwhelmed|falling apart|"
    r"not okay|not ok|i'm struggling|struggling",
    re.IGNORECASE
)

def has_sad_signal(text):
    return bool(_SAD_WORDS_RE.search(text))

# ─────────────────────────────────────────────
# Guardian injection
# ─────────────────────────────────────────────
_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|above|prior|earlier)\s*(instructions?|prompt|rules?|context)?",
    r"disregard\s+(previous|all|above|prior)",
    r"forget\s+(everything|all|your\s+instructions?|what\s+i\s+said)",
    r"override\s+(previous|all|your)\s*(instructions?|rules?|prompt)?",
    r"(act|behave|pretend|roleplay|respond|answer)\s+(as|like|you\s+are|you.re)",
    r"you\s+are\s+now\s+(a|an|the)", r"you\s+are\s+now",
    r"from\s+now\s+on\s+(you|act|be|respond)",
    r"your\s+new\s+(role|persona|identity|name|instructions?)",
    r"switch\s+(role|mode|persona|identity)",
    r"new\s+(instructions?|prompt|system\s+prompt|rules?|guidelines?)",
    r"updated?\s+(instructions?|prompt|rules?|guidelines?)",
    r"(here\s+are|following\s+are)\s+(your\s+)?(new\s+)?(instructions?|rules?)",
    r"developer\s+mode", r"admin\s+mode", r"maintenance\s+mode",
    r"(this\s+is\s+a?\s*)?(test|simulation|demo|drill|exercise)",
    r"pretend\s+(this\s+is\s+(a\s+)?test|you.re\s+not\s+a\s+bot)",
    r"for\s+(testing|training|research|evaluation)\s+purposes?",
    r"jailbreak", r"DAN\b",
    r"(show|print|reveal|display|output|give\s+me|tell\s+me|repeat|share)\s*.{0,30}(prompt|instructions?|system|rules?|guidelines?|context)",
    r"what\s+(are|were)\s+(your|the)\s+(instructions?|rules?|prompt|system)",
    r"base64", r"decode\s+this", r"encoded?\s+(message|payload|instructions?)",
    r"[A-Za-z0-9+/]{20,}={0,2}",
    r"continue\s+but\s+(ignore|forget|disregard)",
    r"(now|then|also|additionally|furthermore)\s+(ignore|forget|disregard)\s+(the|your|all)",
    r"(and\s+)?ignore\s+(the\s+)?(above|previous|prior|last|earlier)",
    r"after\s+(this|that)\s+(ignore|forget|disregard)",
    r"תתנהג כ", r"תשכח\s+(הכל|את\s+הכל|את\s+ההוראות)",
    r"הוראות\s+(חדשות|עדכניות|מעודכנות)", r"אתה\s+עכשיו",
    r"מעתה\s+(אתה|תתנהג|תגיב)", r"התעלם\s+(מ|מה|מכל)",
    r"שכח\s+(הכל|את\s+הכל|את\s+ההוראות)",
    r"(הצג|הראה|גלה|חשוף|כתוב)\s*.{0,30}(פרומפט|הוראות|מערכת|system)",
    r"מה\s+ה(פרומפט|הוראות|מערכת|instructions)",
    r"(בדיקה|ניסוי|סימולציה|תרגיל)\s*(בלבד|בלבד\s*—)?",
    r"<script", r"javascript\s*:", r"\$\{.*?\}",
    r"eval\s*\(", r"exec\s*\(", r"__import__", r"subprocess", r"os\s*\.\s*system",
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
                MSG_BREATHING_WAIT_CONFIRM]:
        _ALLOWED_OUTGOING.add(msg.strip())
    for msg in BREATHING_PARTS + GROUNDING_STEPS:
        _ALLOWED_OUTGOING.add(msg.strip())
    for hint in GROUNDING_HINTS:
        _ALLOWED_OUTGOING.add(GROUNDING_CHAT_REPLY.format(hint=hint).strip())

_build_allowed_outgoing()

def is_allowed_outgoing(text):
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
            "reason":   reason,
            "time":     time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "expires":  time.time() + BLACKLIST_TTL,
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
        now  = time.time()
        raw  = _redis.hgetall(BLACKLIST_KEY)
        exp  = [p for p, v in raw.items() if json.loads(v).get("expires", float("inf")) < now]
        if exp:
            _redis.hdel(BLACKLIST_KEY, *exp)
    except Exception as e:
        log.error("blacklist_clean_error", extra={"err": str(e)})

def _send_admin_alert(phone, reason):
    if not ADMIN_SMS_TO:
        return
    msg = "\u26a0\ufe0f SafeHarbor Alert\nPhone {} BLACKLISTED\nReason: {}\nTime: {}".format(
        phone, reason, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        http_requests.post(WHATSAPP_API_URL, headers=_WA_HEADERS,
            json={"messaging_product": "whatsapp", "recipient_type": "individual",
                  "to": ADMIN_SMS_TO, "type": "text", "text": {"body": msg}}, timeout=10)
    except Exception as e:
        log.error("admin_alert_error", extra={"err": str(e)})

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
                log.warning("meta_rate_limit", extra={"retry_after": wait, "attempt": attempt + 1})
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            return True
        except Exception as e:
            last_err = e
            log.warning("send_attempt_failed", extra={"attempt": attempt + 1, "err": str(e)})
    log.error("send_failed_all_retries", extra={"payload_to": payload.get("to", "?"), "err": str(last_err)})
    return False

def send_message(to, text):
    if not text or not text.strip():
        return
    if not is_allowed_outgoing(text):
        log.warning("guardian_blocked_outgoing", extra={"text": text[:80]})
        return
    _post_with_retry({"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "text", "text": {"body": text.strip()}})

def send_logo(to):
    _post_with_retry({"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "image", "image": {"link": LOGO_URL}})

# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
STATE_KEY_PREFIX = "sh:state:"
SEEN_MSG_TTL_SEC = 120
STATE_TTL_SEC    = 90 * 24 * 3600

_STATE_DEFAULTS = {
    "tool":              "none",
    "step":              0,
    "welcomed":          False,
    "last_msg_time":     0.0,
    "wait_count":        0,
    "grounding_session": 0,
    "sad_count":         0,
    "breathing_active":  False,
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
# Redis reply channel
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
    """polling כל 0.1s — תגובה כמעט מיידית"""
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
# run_breathing — thread יחיד
#
# כל iteration:
#   1. breathing_active=True
#   2. BREATHING_START
#   3. 12 הודעות + "נמשיך?"
#   4. breathing_active=False
#   5. המתן _br_wait_fast(60s)
#   6. yes → continue מיד | no/timeout/abort → עצור
# ─────────────────────────────────────────────
def run_breathing(phone):
    log.info("breathing_start", extra={"phone": phone})

    while True:
        s = get_state(phone)
        if s.get("tool") != "breathing":
            log.info("breathing_exit", extra={"phone": phone})
            return

        set_state(phone, breathing_active=True)
        _br_clear(phone)

        # שלח "בוא נספור" — בכל iteration (גם אחרי "כן")
        send_message(phone, BREATHING_START)

        # 12 הודעות + "נמשיך?"
        for i, part in enumerate(BREATHING_PARTS):
            s = get_state(phone)
            if s.get("tool") != "breathing":
                log.info("breathing_aborted", extra={"phone": phone, "step": i})
                return
            send_message(phone, part)
            if i < len(BREATHING_PARTS) - 1:
                time.sleep(5)

        # ממתין לתשובה
        set_state(phone, breathing_active=False)
        _br_clear(phone)

        reply = _br_wait_fast(phone, timeout=60)
        log.info("breathing_reply", extra={"phone": phone, "reply": reply})

        if reply == "yes":
            continue  # מיד לתחילת iteration — BREATHING_START ישלח שוב

        # לא / timeout / abort
        s = get_state(phone)
        if s.get("tool") == "breathing":
            set_state(phone, tool="none", step=0, breathing_active=False)
            send_message(phone, MSG_BREATHING_STOP)
        _br_clear(phone)
        return

# ─────────────────────────────────────────────
# קרקוע — nudges
# ─────────────────────────────────────────────
def nudge_if_silent_grounding(phone, my_step, my_session):
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "grounding" or s["step"] != my_step or s["grounding_session"] != my_session:
        return
    send_message(phone, GROUNDING_NUDGE_1)
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "grounding" or s["step"] != my_step or s["grounding_session"] != my_session:
        return
    send_message(phone, GROUNDING_NUDGE_2)

def nudge_after_welcome(phone, welcomed_time):
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] == "none" and s["welcomed"] and s["last_msg_time"] <= welcomed_time + 1:
        send_message(phone, MSG_WELCOME_NUDGE)

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
    send_message(phone, MSG_WELCOME)
    _enqueue(nudge_after_welcome, phone, now)

# ─────────────────────────────────────────────
# לוגיקה מרכזית
# ─────────────────────────────────────────────
def handle_message(phone, text):
    with _get_phone_lock(phone):
        _handle_message_inner(phone, text)

def _handle_message_inner(phone, text):
    text = _clean_text(text)
    t    = text.lower()

    # 1. משבר
    if is_crisis(text):
        log.info("crisis_detected", extra={"phone": phone})
        set_state(phone, tool="none", step=0, sad_count=0, breathing_active=False)
        _br_clear(phone)
        send_message(phone, MSG_CRISIS)
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
        send_message(phone, MSG_OFF_TOPIC)
        return

    s["last_msg_time"] = now
    try:
        _redis.set(STATE_KEY_PREFIX + phone, json.dumps(s), ex=STATE_TTL_SEC)
    except Exception as e:
        log.error("set_state_error", extra={"phone": phone, "err": str(e)})

    tool = s["tool"]
    step = s["step"]

    # 5. escalation
    if has_sad_signal(text):
        new_sad = s.get("sad_count", 0) + 1
        set_state(phone, sad_count=new_sad)
        if new_sad >= 3:
            log.info("escalation_crisis", extra={"phone": phone, "sad_count": new_sad})
            set_state(phone, tool="none", step=0, sad_count=0, breathing_active=False)
            _br_clear(phone)
            send_message(phone, MSG_CRISIS)
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
            send_message(phone, MSG_BREATHING_STOP)
            return
        if not s.get("breathing_active"):
            if t in BREATHING_YES_WORDS:
                _br_write(phone, "yes")
                return
            else:
                send_message(phone, MSG_BREATHING_WAIT_CONFIRM)
                return
        return  # breathing_active=True → התעלם

    # ── 8. קרקוע פעיל ──
    if tool == "grounding":
        gs = s["grounding_session"]
        if t in GROUNDING_RESET_WORDS:
            set_state(phone, tool="none", step=0, wait_count=0, grounding_session=gs + 1)
            send_message(phone, MSG_RESET)
            return
        if is_grounding_chat(text):
            send_message(phone, GROUNDING_CHAT_REPLY.format(hint=GROUNDING_HINTS[step]))
            return
        new_gs    = gs + 1
        next_step = step + 1
        if next_step < len(GROUNDING_STEPS):
            set_state(phone, step=next_step, wait_count=0, grounding_session=new_gs)
            send_message(phone, GROUNDING_STEPS[next_step])
            _enqueue(nudge_if_silent_grounding, phone, next_step, new_gs)
        else:
            set_state(phone, tool="none", step=0, wait_count=0, grounding_session=new_gs)
            send_message(phone, MSG_RETURNING)
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
        send_message(phone, GROUNDING_STEPS[0])
        _enqueue(nudge_if_silent_grounding, phone, 0, new_gs)
        return

    # 11. ברכה
    if t in GREET_WORDS:
        send_message(phone, MSG_RETURNING)
        return

    # 12. off-topic
    send_message(phone, MSG_OFF_TOPIC)

_GROUNDING_CHAT_PHRASES = [
    "מה זה", "למה אתה", "מה אתה", "מה את",
    "לא רוצה", "אני רוצה לדבר",
    "תגיד לי", "הסבר לי",
    "i don't want to", "tell me about",
]
_grounding_chat_re = re.compile(
    "|".join(re.escape(p) for p in _GROUNDING_CHAT_PHRASES), re.IGNORECASE
)

def is_grounding_chat(text):
    return bool(_grounding_chat_re.search(text.lower()))

# ─────────────────────────────────────────────
# Admin Dashboard
# ─────────────────────────────────────────────
def _check_admin_key(req):
    return req.headers.get("X-Admin-Key") == ADMIN_API_KEY

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    ip = request.remote_addr or "unknown"
    if not _admin_rate_ok(ip):
        return jsonify({"error": "too many requests"}), 429
    if not _check_admin_key(request):
        return (
            '<html><head><title>SafeHarbor Admin</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;'
            'height:100vh;margin:0;background:#f5f5f5}.box{background:#fff;padding:32px;border-radius:12px;'
            'box-shadow:0 2px 12px #0002;width:340px;text-align:center}h2{margin:0 0 16px;font-size:18px}'
            'p{color:#666;font-size:14px;margin:0}</style></head>'
            '<body><div class="box"><h2>SafeHarbor Admin</h2>'
            '<p>Send <code>X-Admin-Key</code> header to authenticate</p>'
            '</div></body></html>'
        ), 401

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
            'border:none;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:13px">הסר</button>'
            '</td></tr>'
        ).format(phone, info.get("reason", ""), info.get("time_str", ""), phone)

    if not rows:
        rows = '<tr><td colspan="4" style="text-align:center;color:#888;padding:24px">אין מספרים חסומים</td></tr>'

    html = (
        '<html><head><title>SafeHarbor Admin</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:sans-serif;background:#f5f5f5;padding:24px;direction:rtl}'
        'h1{font-size:20px;color:#1e293b;margin-bottom:20px}'
        '.card{background:#fff;border-radius:12px;box-shadow:0 2px 8px #0001;padding:20px;margin-bottom:20px}'
        'table{width:100%;border-collapse:collapse;font-size:14px}'
        'th{text-align:right;padding:10px 12px;background:#f8fafc;color:#64748b;font-weight:600;border-bottom:2px solid #e2e8f0}'
        'td{padding:10px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}'
        '.add-row{display:flex;gap:10px;margin-top:12px}'
        '.add-row input{flex:1;padding:9px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px}'
        '.add-row button{padding:9px 18px;background:#2563eb;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px}'
        '#msg{padding:10px;border-radius:8px;margin-bottom:16px;display:none;font-size:14px}</style></head>'
        '<body><h1>SafeHarbor — ניהול רשימה שחורה</h1><div id="msg"></div>'
        '<div class="card"><table><thead>'
        '<tr><th>מספר טלפון</th><th>סיבה</th><th>תאריך</th><th></th></tr>'
        '</thead><tbody id="bl-table">' + rows + '</tbody></table>'
        '<div class="add-row">'
        '<input id="new-phone" type="text" placeholder="972501234567" dir="ltr">'
        '<button onclick="addPhone()">חסום</button></div></div>'
        '<script>'
        'const AK=document.cookie.match(/adminKey=([^;]+)/)?.[1]||"";'
        'const H={"X-Admin-Key":AK};'
        'function showMsg(t,ok){const e=document.getElementById("msg");e.textContent=t;e.style.display="block";'
        'e.style.background=ok?"#dcfce7":"#fee2e2";e.style.color=ok?"#166534":"#dc2626";'
        'setTimeout(()=>e.style.display="none",3000);}'
        'function removePhone(p){if(!confirm("להסיר "+p+"?"))return;'
        'fetch("/admin/blacklist/"+p,{method:"DELETE",headers:H})'
        '.then(r=>r.json()).then(()=>{showMsg("הוסר: "+p,true);setTimeout(()=>location.reload(),1000);});}'
        'function addPhone(){const p=document.getElementById("new-phone").value.trim();if(!p)return;'
        'fetch("/admin/blacklist/"+p,{method:"POST",headers:{...H,"Content-Type":"application/json"},'
        'body:JSON.stringify({reason:"manual"})})'
        '.then(r=>r.json()).then(()=>{showMsg("נוסף: "+p,true);setTimeout(()=>location.reload(),1000);});}'
        '</script></body></html>'
    )
    return html, 200

@app.route("/admin/blacklist", methods=["GET"])
def admin_list_blacklist():
    if not _check_admin_key(request):
        return jsonify({"error": "unauthorized"}), 401
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
    return jsonify({"status": "removed" if remove_from_blacklist(phone) else "not_found", "phone": phone}), 200

@app.route("/admin/blacklist/<phone>", methods=["POST"])
def admin_add_blacklist(phone):
    if not _check_admin_key(request):
        return jsonify({"error": "unauthorized"}), 401
    reason = request.json.get("reason", "manual") if request.json else "manual"
    add_to_blacklist(phone, reason=reason)
    return jsonify({"status": "blacklisted", "phone": phone}), 200

# ─────────────────────────────────────────────
# Webhook
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
    data = request.get_json(silent=True)
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        msg_id = msg.get("id", "")
                        if msg_id and _is_duplicate_msg(msg_id):
                            log.info("dedup_blocked", extra={"msg_id": msg_id})
                            continue
                        phone = msg["from"]
                        text  = msg["text"]["body"]
                        _msg_executor.submit(handle_message, phone, text)
    except Exception as e:
        log.error("webhook_error", extra={"err": str(e)})
    return jsonify({"status": "ok"}), 200

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
    status = 200 if redis_ok else 503
    return jsonify({
        "status":  "ok" if redis_ok else "degraded",
        "version": "v56",
        "uptime":  int(time.time() - _START_TIME),
        "redis":   "ok" if redis_ok else "error",
        "queue":   "rq" if _USE_RQ else "threadpool",
    }), status

@app.route("/", methods=["GET"])
def root():
    return "SafeHarbor Bot is running v56", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
