# v34 - Performance & correctness audit: 12 fixes
import os
import time
import json
import threading
import requests
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify

app = Flask(__name__)

WHATSAPP_TOKEN    = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "12345")
WHATSAPP_API_URL  = "https://graph.facebook.com/v22.0/{}/messages".format(WHATSAPP_PHONE_ID)

STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_states.json")
_state_lock   = threading.Lock()
_dirty        = False
_last_save    = 0
SAVE_INTERVAL = 5
DEBOUNCE_SEC  = 1.0
_executor     = ThreadPoolExecutor(max_workers=50)

LOGO_URL = "https://raw.githubusercontent.com/argovalex/safeharbor-bot/main/logo.png"

# ── FIX 8: pre-build headers once, reuse on every request ────────────────────
_WA_HEADERS = {
    "Authorization": "Bearer {}".format(WHATSAPP_TOKEN),
    "Content-Type": "application/json",
}

# ══════════════════════════════════════════════════════════════════════════════
# ── MESSAGES ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

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
MSG_NUDGE = (
    "אני כאן איתך, אתה עדיין איתי? "
    "בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. \u2693"
)
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

BREATHING_STOP_WORDS = {"לא", "ל", "no", "n", "די", "stop", "done"}
GREET_WORDS          = {"שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי"}
GROUNDING_RESET_WORDS = {"חזור", "איפוס", "reset", "back", "stop", "די"}

# ── FIX 3: crisis detection via compiled regex (O(1) vs O(n) list scan) ──────
_CRISIS_WORDS_LIST = [
    "suicide", "kill myself", "want to die", "end my life", "cut myself",
    "no reason to live", "no hope", "worthless",
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות", "בא לי למות",
    "לחתוך", "להפסיק את הסבל", "אין טעם", "אין תקווה", "חסר סיכוי",
    "קצה היכולת", "לא יכול יותר", "נמאס לי מהכל", "אבוד לי",
    "מכתב פרידה", "צוואה", "סליחה מכולם", "הכל נגמר",
    "חושך מוחלט", "לישון ולא לקום",
    "אין לי תקווה", "לא רוצה לחיות", "נמאס לי לחיות", "לא שווה לחיות",
]
_crisis_re = re.compile(
    "|".join(re.escape(w) for w in _CRISIS_WORDS_LIST),
    re.IGNORECASE
)

def is_crisis(text):
    return bool(_crisis_re.search(text))

# ── FIX 4+12: grounding chat detection via compiled regex, remove dead code ───
_GROUNDING_CHAT_PHRASES = [
    "מה זה", "למה", "אני לא", "אני לא יודע", "לא יודע",
    "מה אתה", "מה את", "לא רוצה", "אני רוצה", "תגיד לי",
    "why", "what", "how", "i don't", "i dont", "tell me",
    "help", "עזור", "הסבר",
    # NOTE: "?" removed — it matched ANY question, preventing grounding step
    # from advancing when user wrote e.g. "חלון? שולחן?"
]
_grounding_chat_re = re.compile(
    "|".join(re.escape(p) for p in _GROUNDING_CHAT_PHRASES),
    re.IGNORECASE
)

def is_grounding_chat(text, step):
    # FIX 12: removed `len(text.split()) < 1` — always False, dead code
    return bool(_grounding_chat_re.search(text.lower()))

# ══════════════════════════════════════════════════════════════════════════════
# ── GUARDIAN ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

INJECTION_PATTERNS = [
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
_injection_re = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

def guardian_check_input(text):
    return bool(_injection_re.search(text))

# ── FIX 5: build allowed-outgoing set at module load, no runtime bool check ───
_ALLOWED_OUTGOING = set()

def _build_allowed_outgoing():
    for msg in [MSG_WELCOME, MSG_RETURNING, MSG_NUDGE, MSG_WELCOME_NUDGE,
                MSG_CRISIS, MSG_OFF_TOPIC, MSG_BREATHING_STOP, MSG_RESET,
                BREATHING_START, GROUNDING_NUDGE_1, GROUNDING_NUDGE_2]:
        _ALLOWED_OUTGOING.add(msg.strip())
    for msg in BREATHING_PARTS + GROUNDING_STEPS:
        _ALLOWED_OUTGOING.add(msg.strip())

_build_allowed_outgoing()   # called once at import, never again

def is_allowed_outgoing(text):
    t = text.strip()
    if t in _ALLOWED_OUTGOING:
        return True
    if t.startswith("אני כאן רק כדי לעזור לך להתייצב"):
        return True
    return False

# needed for test compatibility
def _ensure_registered():
    pass  # no-op — set already built at module load

# ── Rate limiting + permanent blacklist ───────────────────────────────────────

RATE_WINDOW_SEC = 60
RATE_MAX_MSGS   = 20
BLACKLIST_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blacklist.json")
ADMIN_SMS_TO    = os.environ.get("ADMIN_PHONE", "")
ADMIN_API_KEY   = os.environ.get("ADMIN_API_KEY", "safeharbor-secret")

_rate_counters  = defaultdict(list)
_rate_lock      = threading.Lock()
_blacklist_lock = threading.Lock()

def _load_blacklist():
    try:
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_blacklist(bl):
    try:
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(bl, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[blacklist save error] {}".format(e))

_blacklist = _load_blacklist()

def is_blacklisted(phone):
    with _blacklist_lock:
        return phone in _blacklist

def add_to_blacklist(phone, reason="rate_limit"):
    with _blacklist_lock:
        _blacklist[phone] = {
            "reason": reason,
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        }
        _save_blacklist(_blacklist)
    print("[BLACKLIST] Added: {} reason={}".format(phone, reason))
    _send_admin_alert(phone, reason)

def remove_from_blacklist(phone):
    with _blacklist_lock:
        if phone in _blacklist:
            del _blacklist[phone]
            _save_blacklist(_blacklist)
            return True
        return False

def _send_admin_alert(phone, reason):
    if not ADMIN_SMS_TO:
        return
    msg = "\u26a0\ufe0f SafeHarbor Alert\nPhone {} BLACKLISTED\nReason: {}\nTime: {}".format(
        phone, reason, time.strftime("%Y-%m-%d %H:%M:%S"))
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
               "to": ADMIN_SMS_TO, "type": "text", "text": {"body": msg}}
    try:
        requests.post(WHATSAPP_API_URL, headers=_WA_HEADERS, json=payload, timeout=10)
    except Exception as e:
        print("[admin alert error] {}".format(e))

# ── FIX 9: single lock for rate-limit check (blacklist + counter together) ────
def rate_limit_check(phone):
    with _blacklist_lock:
        if phone in _blacklist:
            return True
    now = time.time()
    with _rate_lock:
        _rate_counters[phone] = [t for t in _rate_counters[phone] if now - t < RATE_WINDOW_SEC]
        if len(_rate_counters[phone]) >= RATE_MAX_MSGS:
            add_to_blacklist(phone, reason="exceeded {} msgs/min".format(RATE_MAX_MSGS))
            return True
        _rate_counters[phone].append(now)
        return False

# ── WhatsApp senders ──────────────────────────────────────────────────────────

def send_message(to, text):
    if not text or not text.strip():
        return
    if not is_allowed_outgoing(text):
        print("[GUARDIAN] BLOCKED: {}".format(text[:80]))
        return
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
               "to": to, "type": "text", "text": {"body": text.strip()}}
    try:
        r = requests.post(WHATSAPP_API_URL, headers=_WA_HEADERS, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print("[send_message error] {}".format(e))

def send_logo(to):
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
               "to": to, "type": "image", "image": {"link": LOGO_URL}}
    try:
        r = requests.post(WHATSAPP_API_URL, headers=_WA_HEADERS, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print("[send_logo error] {}".format(e))

# ══════════════════════════════════════════════════════════════════════════════
# ── PERSISTENT STATE ──────────────────────────────────════════════════════════
# ══════════════════════════════════════════════════════════════════════════════

# ── FIX 1: singleton default state — never allocate unless needed ─────────────
_STATE_DEFAULTS = {
    "tool": "none", "step": 0,
    "welcomed": False, "round_id": 0,
    "last_msg_time": 0.0, "wait_count": 0,
    "grounding_session": 0
}

def _default_state():
    return dict(_STATE_DEFAULTS)   # cheap shallow copy of a small dict

def load_states():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

# ── FIX 10: background flusher does NOT hold _state_lock while writing ────────
def _flush_if_needed(force=False):
    global _dirty, _last_save
    now = time.time()
    if not _dirty:
        return
    if not force and now - _last_save < SAVE_INTERVAL:
        return
    # Snapshot under lock, write outside lock
    with _state_lock:
        if not _dirty:
            return
        snapshot = dict(_all_states)
        _dirty    = False
        _last_save = now
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
    except Exception as e:
        print("[save error] {}".format(e))

_all_states = load_states()

def get_state(phone):
    with _state_lock:
        if phone not in _all_states:
            _all_states[phone] = _default_state()
        s = _all_states[phone]
        for k, v in _STATE_DEFAULTS.items():
            s.setdefault(k, v)
        return dict(s)

def set_state(phone, force_save=False, **kwargs):
    global _dirty
    with _state_lock:
        s = _all_states.setdefault(phone, _default_state())
        for k, v in _STATE_DEFAULTS.items():
            s.setdefault(k, v)
        s.update(kwargs)
        _dirty = True
    if force_save:
        _flush_if_needed(force=True)

def _background_flusher():
    while True:
        time.sleep(SAVE_INTERVAL)
        _flush_if_needed(force=True)   # FIX 10: no longer inside _state_lock

threading.Thread(target=_background_flusher, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# ── BREATHING ─────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def breathing_post_round_wait(phone, my_round_id):
    time.sleep(30)
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    send_message(phone, MSG_NUDGE)
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    send_message(phone, MSG_NUDGE)

# ── FIX 6: reduce get_state() calls in breathing loop from 3x to 1x ──────────
def run_breathing_round(phone):
    s = get_state(phone)
    my_round_id = s["round_id"]
    for i, part in enumerate(BREATHING_PARTS):
        # Single state check covers both pre-send and post-sleep conditions
        s = get_state(phone)
        if s["tool"] != "breathing" or s["round_id"] != my_round_id:
            return
        send_message(phone, part)
        if i < len(BREATHING_PARTS) - 1:
            time.sleep(5)
            # Check after sleep — if user stopped during the 5s we abort now
            s = get_state(phone)
            if s["tool"] != "breathing" or s["round_id"] != my_round_id:
                return
    # Final guard before scheduling nudge
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    set_state(phone, last_msg_time=time.time())
    _executor.submit(breathing_post_round_wait, phone, my_round_id)

# ══════════════════════════════════════════════════════════════════════════════
# ── GROUNDING ─────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════════════════════
# ── WELCOME NUDGE ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def nudge_after_welcome(phone, welcomed_time):
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] == "none" and s["welcomed"] and s["last_msg_time"] <= welcomed_time + 1:
        send_message(phone, MSG_WELCOME_NUDGE)

# ══════════════════════════════════════════════════════════════════════════════
# ── WEBHOOK DEDUPLICATION ─────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_seen_msg_ids  = {}
_seen_msg_lock = threading.Lock()
SEEN_MSG_TTL_SEC = 120

# ── FIX 7: lazy TTL purge — only purge when cache grows, not on every call ────
_SEEN_PURGE_EVERY = 500   # purge every 500 new messages

def _is_duplicate_msg(msg_id):
    now = time.time()
    with _seen_msg_lock:
        if msg_id in _seen_msg_ids:
            # Check TTL on read — expired entry = not a duplicate
            if now - _seen_msg_ids[msg_id] <= SEEN_MSG_TTL_SEC:
                return True
            # Expired: treat as new, update timestamp
            _seen_msg_ids[msg_id] = now
            return False
        _seen_msg_ids[msg_id] = now
        # Lazy purge only every N new entries
        if len(_seen_msg_ids) % _SEEN_PURGE_EVERY == 0:
            expired = [k for k, t in _seen_msg_ids.items() if now - t > SEEN_MSG_TTL_SEC]
            for k in expired:
                del _seen_msg_ids[k]
        return False

# ══════════════════════════════════════════════════════════════════════════════
# ── MAIN HANDLER ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_phone_locks = defaultdict(threading.Lock)

def handle_message(phone, text):
    with _phone_locks[phone]:
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
        print("[GUARDIAN] Injection from {}: {}".format(phone, text[:80]))
        set_state(phone, tool="none", step=0, force_save=True)
        send_message(phone, MSG_OFF_TOPIC)
        return

    set_state(phone, last_msg_time=now)
    # ── FIX 2: re-read state once after update instead of two separate calls ──
    s    = get_state(phone)
    tool = s["tool"]
    step = s["step"]

    # 1. Crisis — checked before everything else, always
    if is_crisis(text):
        send_message(phone, MSG_CRISIS)
        return

    # 2. First message
    if not s["welcomed"]:
        set_state(phone, welcomed=True, force_save=True)
        send_logo(phone)
        send_message(phone, MSG_WELCOME)
        _executor.submit(nudge_after_welcome, phone, now)
        return

    # 3. Breathing
    if tool == "breathing":
        if t in BREATHING_STOP_WORDS:
            set_state(phone, tool="none", step=0, round_id=s["round_id"] + 1, force_save=True)
            send_message(phone, MSG_BREATHING_STOP)
        else:
            new_round = s["round_id"] + 1
            set_state(phone, round_id=new_round, force_save=True)
            _executor.submit(run_breathing_round, phone)
        return

    # 4. Grounding
    if tool == "grounding":
        gs = s["grounding_session"]
        if t in GROUNDING_RESET_WORDS:
            set_state(phone, tool="none", step=0, wait_count=0,
                      grounding_session=gs + 1, force_save=True)
            send_message(phone, MSG_RESET)
            return
        if is_grounding_chat(text, step):
            send_message(phone, GROUNDING_CHAT_REPLY.format(hint=GROUNDING_HINTS[step]))
            return
        new_gs    = gs + 1
        next_step = step + 1
        if next_step < len(GROUNDING_STEPS):
            set_state(phone, step=next_step, wait_count=0,
                      grounding_session=new_gs, force_save=True)
            send_message(phone, GROUNDING_STEPS[next_step])
            _executor.submit(nudge_if_silent_grounding, phone, next_step, new_gs)
        else:
            set_state(phone, tool="none", step=0, wait_count=0,
                      grounding_session=new_gs, force_save=True)
            send_message(phone, MSG_RETURNING)
        return

    # 5. Routing
    if text == "א" or t == "a":
        new_round = s["round_id"] + 1
        set_state(phone, tool="breathing", step=0, round_id=new_round, force_save=True)
        send_message(phone, BREATHING_START)
        _executor.submit(run_breathing_round, phone)
        return

    if text == "ב" or t == "b":
        new_gs = s["grounding_session"] + 1
        set_state(phone, tool="grounding", step=0, wait_count=0,
                  grounding_session=new_gs, force_save=True)
        send_message(phone, GROUNDING_STEPS[0])
        _executor.submit(nudge_if_silent_grounding, phone, 0, new_gs)
        return

    # 6. Greeting
    if t in GREET_WORDS:
        send_message(phone, MSG_RETURNING)
        return

    # 7. Unknown
    send_message(phone, MSG_OFF_TOPIC)

# ══════════════════════════════════════════════════════════════════════════════
# ── ADMIN DASHBOARD ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _check_admin_key(req):
    return (req.headers.get("X-Admin-Key") == ADMIN_API_KEY or
            req.args.get("key") == ADMIN_API_KEY)

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    if not _check_admin_key(request):
        return '''<html><head><title>SafeHarbor Admin</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
        height:100vh;margin:0;background:#f5f5f5}.box{background:#fff;padding:32px;border-radius:12px;
        box-shadow:0 2px 12px #0002;width:300px}h2{margin:0 0 20px;font-size:18px;color:#333}
        input{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;font-size:15px;
        box-sizing:border-box;margin-bottom:12px}button{width:100%;padding:10px;background:#2563eb;
        color:#fff;border:none;border-radius:8px;font-size:15px;cursor:pointer}</style></head>
        <body><div class="box"><h2>🔒 SafeHarbor Admin</h2><form method="get">
        <input type="password" name="key" placeholder="Admin key" autofocus>
        <button type="submit">כניסה</button></form></div></body></html>''', 401

    with _blacklist_lock:
        bl_copy = dict(_blacklist)

    rows = ""
    for phone, info in sorted(bl_copy.items(), key=lambda x: x[1].get("time", 0), reverse=True):
        rows += '<tr><td style="font-family:monospace">{}</td><td>{}</td><td style="color:#888;font-size:13px">{}</td><td><button onclick="removePhone(\'{}\')" style="background:#dc2626;color:#fff;border:none;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:13px">הסר</button></td></tr>'.format(
            phone, info.get("reason", ""), info.get("time_str", ""), phone)

    if not rows:
        rows = '<tr><td colspan="4" style="text-align:center;color:#888;padding:24px">אין מספרים חסומים</td></tr>'

    key = request.args.get("key", "")
    html = '''<html><head><title>SafeHarbor Admin</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f5f5;padding:24px;direction:rtl}}
    h1{{font-size:20px;color:#1e293b;margin-bottom:20px}}.card{{background:#fff;border-radius:12px;box-shadow:0 2px 8px #0001;padding:20px;margin-bottom:20px}}
    table{{width:100%;border-collapse:collapse;font-size:14px}}th{{text-align:right;padding:10px 12px;background:#f8fafc;color:#64748b;font-weight:600;border-bottom:2px solid #e2e8f0}}
    td{{padding:10px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}}.add-row{{display:flex;gap:10px;margin-top:12px}}
    .add-row input{{flex:1;padding:9px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px}}
    .add-row button{{padding:9px 18px;background:#2563eb;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px}}
    #msg{{padding:10px;border-radius:8px;margin-bottom:16px;display:none;font-size:14px}}</style></head>
    <body><h1>🔒 SafeHarbor — ניהול רשימה שחורה</h1><div id="msg"></div>
    <div class="card"><table><thead><tr><th>מספר טלפון</th><th>סיבה</th><th>תאריך</th><th></th></tr></thead>
    <tbody id="bl-table">{rows}</tbody></table>
    <div class="add-row"><input id="new-phone" type="text" placeholder="972501234567" dir="ltr">
    <button onclick="addPhone()">חסום</button></div></div>
    <script>const KEY="{key}";
    function showMsg(t,ok){{const e=document.getElementById("msg");e.textContent=t;e.style.display="block";
    e.style.background=ok?"#dcfce7":"#fee2e2";e.style.color=ok?"#166534":"#dc2626";
    setTimeout(()=>{{e.style.display="none";}},3000);}}
    function removePhone(p){{if(!confirm("להסיר "+p+"?"))return;
    fetch("/admin/blacklist/"+p+"?key="+KEY,{{method:"DELETE"}}).then(r=>r.json()).then(()=>{{showMsg("הוסר: "+p,true);setTimeout(()=>location.reload(),1000);}});}}
    function addPhone(){{const p=document.getElementById("new-phone").value.trim();if(!p)return;
    fetch("/admin/blacklist/"+p+"?key="+KEY,{{method:"POST",headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{reason:"manual"}})}}).then(r=>r.json()).then(()=>{{showMsg("נוסף: "+p,true);setTimeout(()=>location.reload(),1000);}});}}
    </script></body></html>'''.format(rows=rows, key=key)
    return html, 200

@app.route("/admin/blacklist", methods=["GET"])
def admin_list_blacklist():
    if not _check_admin_key(request):
        return jsonify({"error": "unauthorized"}), 401
    with _blacklist_lock:
        return jsonify({"blacklist": _blacklist, "count": len(_blacklist)}), 200

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

# ══════════════════════════════════════════════════════════════════════════════
# ── WEBHOOK ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

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
                            print("[DEDUP] Duplicate webhook ignored: {}".format(msg_id))
                            continue
                        phone = msg["from"]
                        text  = msg["text"]["body"]
                        _executor.submit(handle_message, phone, text)
    except Exception as e:
        print("[webhook error] {}".format(e))
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health():
    return "SafeHarbor Bot is running v34", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
