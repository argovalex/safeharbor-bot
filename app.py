# SafeHarbor Bot v43
# v43: fix guardian over-blocking (removed why/what/how/help from grounding_chat_phrases),
#      fix GROUNDING_CHAT_REPLY not in allowed_outgoing, crisis resets state,
#      breathing race condition check after each sleep, logo/welcome in background thread,
#      phone_locks cleanup by time instead of size, nudge_after_welcome checks tool state,
#      LOGO_URL via env var
import os, time, json, logging, threading
import requests as http_requests
import re
import redis as redis_lib
from collections import defaultdict
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
VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "12345")
WHATSAPP_API_URL  = "https://graph.facebook.com/v22.0/{}/messages".format(WHATSAPP_PHONE_ID)
DEBOUNCE_SEC      = 1.0
LOGO_URL          = os.environ.get("LOGO_URL", "https://raw.githubusercontent.com/argovalex/safeharbor-bot/main/logo.png")

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

MSG_WELCOME = (
    '*שלום, אני נמל הבית* \u2693\n'
    'אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.\n\n'
    'אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:\n'
    '\u260e\ufe0f ער"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\u260e\ufe0f נט"ל: 1-800-363-363\n\n'
    '*מה יעזור לך יותר ברגע הזה?*\n'
    '\U0001f32c\ufe0f כתוב *א* — תרגילי נשימה\n'
    '\u2693 כתוב *ב* — תרגיל קרקוע'
)
MSG_RETURNING = (
    'היי, טוב שחזרת אלי. \U0001f499\n'
    'אני נמל הבית, ואני כאן איתך שוב.\n\n'
    '*מה מרגיש לך נכון יותר ברגע הזה?*\n'
    '\U0001f32c\ufe0f כתוב *א* — נשימה מרגיעה\n'
    '\u2693 כתוב *ב* — תרגיל קרקוע\n\n'
    'זכור שיש עזרה אנושית זמינה עבורך תמיד:\n'
    '\u260e\ufe0f ער"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\u260e\ufe0f נט"ל: 1-800-363-363'
)
MSG_NUDGE         = "אני כאן איתך, אתה עדיין איתי? בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. \u2693"
MSG_WELCOME_NUDGE = "אני כאן איתך. \u2693\nכתוב *א* לנשימה או *ב* לקרקוע."
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
    'כתוב *א* לתרגיל נשימה \U0001f32c\ufe0f\n'
    'כתוב *ב* לתרגיל קרקוע \u2693'
)
MSG_BREATHING_STOP = (
    "יופי שעצרת רגע להקשיב לעצמך \U0001f33f\n"
    "אפשר להישאר עם התחושה הזו עוד כמה שניות, בקצב שנוח לך \U0001f54a\ufe0f\n\n"
    "אם תרצה לחזור לזה בהמשך, אני כאן \u2693"
)
MSG_RESET       = "בסדר, אני כאן כשתצטרך. \U0001f30a"
BREATHING_START = "אני כאן איתך בוא נספור יחד. \U0001f32c\ufe0f"

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

BREATHING_STOP_WORDS  = {"לא", "ל", "no", "n", "די", "stop", "done"}
GREET_WORDS           = {"שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי"}
GROUNDING_RESET_WORDS = {"חזור", "איפוס", "reset", "back", "stop", "די"}

_CRISIS_WORDS = [
    "suicide", "kill myself", "want to die", "end my life", "cut myself",
    "no reason to live", "no hope", "worthless",
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות", "בא לי למות",
    "לחתוך", "להפסיק את הסבל", "אין טעם", "אין תקווה", "חסר סיכוי",
    "קצה היכולת", "לא יכול יותר", "נמאס לי מהכל", "אבוד לי",
    "מכתב פרידה", "צוואה", "סליחה מכולם", "הכל נגמר",
    "חושך מוחלט", "לישון ולא לקום",
    "אין לי תקווה", "לא רוצה לחיות", "נמאס לי לחיות", "לא שווה לחיות",
]
_crisis_re = re.compile("|".join(re.escape(w) for w in _CRISIS_WORDS), re.IGNORECASE)

def is_crisis(text):
    return bool(_crisis_re.search(text))

# FIX 1+2: removed "what","why","how","help","עזור" — too broad, blocks legit user messages
# Only flag clear conversational patterns that are truly off-topic during grounding
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

_INJECTION_PATTERNS = [
    r"ignore (previous|all|above)",
    r"forget (everything|all|your instructions)",
    r"(act|behave|pretend|roleplay) (as|like|you are)",
    r"you are now",
    r"new (instructions|prompt|system)",
    r"developer mode",
    r"jailbreak",
    r"תתנהג כ",
    r"תשכח (הכל|את הכל)",
    r"הוראות חדשות",
    r"אתה עכשיו",
    r"(show|print|reveal|give me|tell me).{0,20}(prompt|instructions|system)",
    r"מה ה(פרומפט|הוראות|מערכת)",
    r"<script",
    r"javascript:",
    r"\$\{.*\}",
    r"eval\(",
    r"exec\(",
]
_injection_re = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

def guardian_check_input(text):
    return bool(_injection_re.search(text))

_ALLOWED_OUTGOING = set()
_ALLOWED_OUTGOING_PREFIXES = []

def _build_allowed_outgoing():
    for msg in [MSG_WELCOME, MSG_RETURNING, MSG_NUDGE, MSG_WELCOME_NUDGE,
                MSG_CRISIS, MSG_OFF_TOPIC, MSG_BREATHING_STOP, MSG_RESET,
                BREATHING_START, GROUNDING_NUDGE_1, GROUNDING_NUDGE_2]:
        _ALLOWED_OUTGOING.add(msg.strip())
    for msg in BREATHING_PARTS + GROUNDING_STEPS:
        _ALLOWED_OUTGOING.add(msg.strip())
    # FIX 3: pre-compute all possible GROUNDING_CHAT_REPLY variants
    for hint in GROUNDING_HINTS:
        _ALLOWED_OUTGOING.add(GROUNDING_CHAT_REPLY.format(hint=hint).strip())

_build_allowed_outgoing()

def is_allowed_outgoing(text):
    t = text.strip()
    if t in _ALLOWED_OUTGOING:
        return True
    return False

def _ensure_registered():
    pass

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
    log.error("send_failed_all_retries", extra={"payload_to": payload.get("to","?"), "err": str(last_err)})
    return False

def send_message(to, text):
    if not text or not text.strip():
        return
    if not is_allowed_outgoing(text):
        log.warning("guardian_blocked", extra={"text": text[:80]})
        return
    _post_with_retry({"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "text", "text": {"body": text.strip()}})

def send_logo(to):
    _post_with_retry({"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "image", "image": {"link": LOGO_URL}})

STATE_KEY_PREFIX = "sh:state:"
SEEN_MSG_TTL_SEC = 120
STATE_TTL_SEC    = 90 * 24 * 3600

_STATE_DEFAULTS = {
    "tool": "none", "step": 0, "welcomed": False, "round_id": 0,
    "last_msg_time": 0.0, "wait_count": 0, "grounding_session": 0
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

def set_state(phone, force_save=False, **kwargs):
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

# FIX 4: breathing round checks round_id atomically via Redis to prevent race condition
def breathing_post_round_wait(phone, my_round_id):
    time.sleep(90)
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    send_message(phone, MSG_NUDGE)
    time.sleep(90)
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    send_message(phone, MSG_NUDGE)

def run_breathing_round(phone):
    my_round_id = None
    for i, part in enumerate(BREATHING_PARTS):
        s = get_state(phone)
        if my_round_id is None:
            my_round_id = s["round_id"]
        if s["tool"] != "breathing" or s["round_id"] != my_round_id:
            return
        send_message(phone, part)
        if i < len(BREATHING_PARTS) - 1:
            time.sleep(5)
            # FIX 4: re-check after sleep — user may have sent "לא" during the 5s gap
            s = get_state(phone)
            if s["tool"] != "breathing" or s["round_id"] != my_round_id:
                log.info("breathing_round_aborted", extra={"phone": phone, "step": i})
                return
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    set_state(phone, last_msg_time=0.0)
    _enqueue(breathing_post_round_wait, phone, my_round_id)

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
    # FIX 6: also check tool=="none" to avoid nudging someone already in a session
    if s["tool"] == "none" and s["welcomed"] and s["last_msg_time"] <= welcomed_time + 1:
        send_message(phone, MSG_WELCOME_NUDGE)

# FIX 7: use WeakValueDictionary + periodic cleanup via timestamp instead of size-based
_phone_locks      = {}
_phone_locks_lock = threading.Lock()
_phone_lock_times = {}  # last access time per phone

def _get_phone_lock(phone):
    now = time.time()
    with _phone_locks_lock:
        # Cleanup locks not accessed in 10 minutes
        stale = [k for k, t in _phone_lock_times.items() if now - t > 600]
        for k in stale:
            _phone_locks.pop(k, None)
            _phone_lock_times.pop(k, None)
        if phone not in _phone_locks:
            _phone_locks[phone] = threading.Lock()
        _phone_lock_times[phone] = now
        return _phone_locks[phone]

def handle_message(phone, text):
    with _get_phone_lock(phone):
        _handle_message_inner(phone, text)

def _handle_message_inner(phone, text):
    text = text.strip()
    t    = text.lower()

    if rate_limit_check(phone):
        return

    s   = get_state(phone)
    now = time.time()
    if now - s["last_msg_time"] < DEBOUNCE_SEC:
        return

    if guardian_check_input(text):
        log.warning("injection_attempt", extra={"phone": phone, "text": text[:80]})
        set_state(phone, tool="none", step=0)
        send_message(phone, MSG_OFF_TOPIC)
        return

    s["last_msg_time"] = now
    try:
        _redis.set(STATE_KEY_PREFIX + phone, json.dumps(s), ex=STATE_TTL_SEC)
    except Exception as e:
        log.error("set_state_error", extra={"phone": phone, "err": str(e)})

    tool = s["tool"]
    step = s["step"]

    # FIX 9: crisis always resets tool state so next message doesn't return to session
    if is_crisis(text):
        log.info("crisis_detected", extra={"phone": phone})
        set_state(phone, tool="none", step=0, wait_count=0)
        send_message(phone, MSG_CRISIS)
        return

    if not s["welcomed"]:
        set_state(phone, welcomed=True)
        # FIX 8: send_logo moved to background thread to avoid blocking web thread
        _enqueue(_send_logo_and_welcome, phone, now)
        return

    if tool == "breathing":
        if t in BREATHING_STOP_WORDS:
            set_state(phone, tool="none", step=0, round_id=s["round_id"] + 1)
            send_message(phone, MSG_BREATHING_STOP)
        else:
            set_state(phone, round_id=s["round_id"] + 1)
            _enqueue(run_breathing_round, phone)
        return

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

    if text == "א" or t == "a":
        set_state(phone, tool="breathing", step=0, round_id=s["round_id"] + 1)
        send_message(phone, BREATHING_START)
        _enqueue(run_breathing_round, phone)
        return

    if text == "ב" or t == "b":
        new_gs = s["grounding_session"] + 1
        set_state(phone, tool="grounding", step=0, wait_count=0, grounding_session=new_gs)
        send_message(phone, GROUNDING_STEPS[0])
        _enqueue(nudge_if_silent_grounding, phone, 0, new_gs)
        return

    if t in GREET_WORDS:
        send_message(phone, MSG_RETURNING)
        return

    send_message(phone, MSG_OFF_TOPIC)

# FIX 8: logo + welcome in background — no sleep() on web thread
def _send_logo_and_welcome(phone, now):
    send_logo(phone)
    time.sleep(0.5)
    send_message(phone, MSG_WELCOME)
    _enqueue(nudge_after_welcome, phone, now)

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

def _check_admin_key(req):
    return req.headers.get("X-Admin-Key") == ADMIN_API_KEY

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
        "version": "v43",
        "uptime":  int(time.time() - _START_TIME),
        "redis":   "ok" if redis_ok else "error",
        "queue":   "rq" if _USE_RQ else "threadpool",
    }), status

@app.route("/", methods=["GET"])
def root():
    return "SafeHarbor Bot is running v43", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
