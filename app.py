# v28 - Fix: NameError in _register_allowed_messages (MSG_WELCOME_NUDGE defined after call)
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

_executor = ThreadPoolExecutor(max_workers=50)

# ══════════════════════════════════════════════════════════════════════════════
# ── GUARDIAN ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

# א) Input injection defense — phrases that try to hijack bot behavior
INJECTION_PATTERNS = [
    # Prompt injection attempts
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
    # Attempts to extract system info
    r"(show|print|reveal|give me|tell me).{0,20}(prompt|instructions|system)",
    r"מה ה(פרומפט|הוראות|מערכת)",
    # Script/code injection
    r"<script",
    r"javascript:",
    r"\$\{.*\}",
    r"eval\(",
    r"exec\(",
]

_injection_re = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

def guardian_check_input(text):
    """Returns True if input is a suspected injection attempt."""
    return bool(_injection_re.search(text))

# ב) Output validation — allowed message templates (exact strings the bot may send)
# Build a set of all valid outgoing message prefixes/hashes at startup
_ALLOWED_OUTGOING = set()

def _register_allowed_messages():
    """Register all valid bot messages. Called once at startup."""
    from_lists = [
        [MSG_WELCOME, MSG_RETURNING, MSG_NUDGE, MSG_CRISIS,
         MSG_OFF_TOPIC, MSG_BREATHING_STOP, MSG_RESET, BREATHING_START,
         GROUNDING_NUDGE_1, GROUNDING_NUDGE_2],
        BREATHING_PARTS,
        GROUNDING_STEPS,
    ]
    for lst in from_lists:
        for msg in lst:
            _ALLOWED_OUTGOING.add(msg.strip())
    # Dynamic messages with format placeholders
    _ALLOWED_OUTGOING.add("__GROUNDING_CHAT_REPLY__")   # validated separately
    # Added after messages are defined
    _ALLOWED_OUTGOING.add("אני כאן איתך. \u2693\nכתוב *א* לנשימה או *ב* לקרקוע.")

def is_allowed_outgoing(text):
    """Returns True if the message is a known valid bot response."""
    t = text.strip()
    if t in _ALLOWED_OUTGOING:
        return True
    # Allow dynamic GROUNDING_CHAT_REPLY (contains formatted hint)
    if t.startswith("אני כאן רק כדי לעזור לך להתייצב"):
        return True
    return False

# ── ג) Rate limiting + permanent blacklist ────────────────────────────────────

RATE_WINDOW_SEC  = 60
RATE_MAX_MSGS    = 20
BLACKLIST_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blacklist.json")
ADMIN_SMS_TO     = os.environ.get("ADMIN_PHONE", "")   # your phone number e.g. 972501234567
ADMIN_API_KEY    = os.environ.get("ADMIN_API_KEY", "safeharbor-secret")  # for admin endpoints

_rate_counters   = defaultdict(list)
_rate_lock       = threading.Lock()
_blacklist_lock  = threading.Lock()

def _load_blacklist():
    try:
        if os.path.exists(BLACKLIST_FILE):
            with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}   # {phone: {"reason": str, "time": epoch}}

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
    _send_admin_sms_alert(phone, reason)

def remove_from_blacklist(phone):
    with _blacklist_lock:
        if phone in _blacklist:
            del _blacklist[phone]
            _save_blacklist(_blacklist)
            return True
        return False

def _send_admin_sms_alert(phone, reason):
    """Send SMS to admin via WhatsApp (uses the same bot number)."""
    if not ADMIN_SMS_TO:
        return
    msg = (
        "\u26a0\ufe0f SafeHarbor Alert\n"
        "Phone {} was BLACKLISTED\n"
        "Reason: {}\n"
        "Time: {}"
    ).format(phone, reason, time.strftime("%Y-%m-%d %H:%M:%S"))
    headers = {
        "Authorization": "Bearer {}".format(WHATSAPP_TOKEN),
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": ADMIN_SMS_TO,
        "type": "text",
        "text": {"body": msg}
    }
    try:
        requests.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print("[admin sms error] {}".format(e))

def rate_limit_check(phone):
    """
    Returns True if user should be blocked.
    After exceeding RATE_MAX_MSGS in window → permanent blacklist + SMS alert.
    """
    if is_blacklisted(phone):
        return True
    now = time.time()
    with _rate_lock:
        _rate_counters[phone] = [
            t for t in _rate_counters[phone] if now - t < RATE_WINDOW_SEC
        ]
        if len(_rate_counters[phone]) >= RATE_MAX_MSGS:
            # Exceeded limit → permanent blacklist
            add_to_blacklist(phone, reason="exceeded {} msgs/min".format(RATE_MAX_MSGS))
            return True
        _rate_counters[phone].append(now)
        return False

LOGO_URL = "https://raw.githubusercontent.com/argovalex/safeharbor-bot/main/logo.png"

# Guardian-wrapped send_message
def send_message(to, text):
    """Send only if text is a known valid bot response."""
    if not text or not text.strip():
        return
    if not is_allowed_outgoing(text):
        print("[GUARDIAN] BLOCKED outgoing: {}".format(text[:80]))
        return
    headers = {
        "Authorization": "Bearer {}".format(WHATSAPP_TOKEN),
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to, "type": "text",
        "text": {"body": text.strip()}
    }
    try:
        r = requests.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print("[send_message error] {}".format(e))

def send_logo(to):
    """Send the SafeHarbor logo image as welcome visual."""
    headers = {
        "Authorization": "Bearer {}".format(WHATSAPP_TOKEN),
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": {"link": LOGO_URL}
    }
    try:
        r = requests.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print("[send_logo error] {}".format(e))

# ══════════════════════════════════════════════════════════════════════════════
# ── PERSISTENT STATE ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _default_state():
    return {
        "tool": "none", "step": 0,
        "welcomed": False, "round_id": 0,
        "last_msg_time": 0.0, "wait_count": 0,
        "grounding_session": 0
    }

def load_states():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _flush_if_needed(force=False):
    global _dirty, _last_save
    now = time.time()
    if _dirty and (force or now - _last_save >= SAVE_INTERVAL):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(_all_states, f, ensure_ascii=False)
            _last_save = now
            _dirty = False
        except Exception as e:
            print("[save_states error] {}".format(e))

_all_states = load_states()

def get_state(phone):
    with _state_lock:
        if phone not in _all_states:
            _all_states[phone] = _default_state()
        s = _all_states[phone]
        for k, v in _default_state().items():
            s.setdefault(k, v)
        return dict(s)

def set_state(phone, force_save=False, **kwargs):
    global _dirty
    with _state_lock:
        s = _all_states.setdefault(phone, _default_state())
        for k, v in _default_state().items():
            s.setdefault(k, v)
        s.update(kwargs)
        _dirty = True
        _flush_if_needed(force=force_save)

def _background_flusher():
    while True:
        time.sleep(SAVE_INTERVAL)
        with _state_lock:
            _flush_if_needed(force=True)

threading.Thread(target=_background_flusher, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# ── MESSAGES ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

MSG_WELCOME = (
    '*שלום, אני נמל הבית* \u2693\n'
    'אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.\n\n'
    'אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:\n'
    '☎️ ער"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '☎️ נט"ל: 1-800-363-363\n\n'
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
    '☎️ ער"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '☎️ נט"ל: 1-800-363-363'
)
MSG_NUDGE = (
    "אני כאן איתך, אתה עדיין איתי? "
    "בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. \u2693"
)
MSG_CRISIS = (
    'אני מבינה שאתה עובר רגע קשה מאוד. אני כאן איתך. \U0001f499\n\n'
    '*יש מי שרוצה לעזור לך — פנה אליהם עכשיו:*\n'
    '☎️ ער"ן: 1201\n'
    '\U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '☎️ נט"ל: 1-800-363-363'
)
MSG_OFF_TOPIC = (
    'אני כאן כדי לעזור לך להתייצב. \u2693\n\n'
    'כתוב *א* לתרגיל נשימה \U0001f32c\ufe0f\n'
    'כתוב *ב* לתרגיל קרקוע \u2693'
)
MSG_WELCOME_NUDGE = "אני כאן איתך. \u2693\nכתוב *א* לנשימה או *ב* לקרקוע."
MSG_RESET          = "בסדר, אני כאן כשתצטרך. \U0001f30a"
BREATHING_START    = "אני כאן איתך בוא נספור יחד. \U0001f32c\ufe0f"

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
GROUNDING_CHAT_PHRASES = [
    "מה זה","למה","אני לא","אני לא יודע","לא יודע",
    "מה אתה","מה את","לא רוצה","אני רוצה","תגיד לי",
    "why","what","how","i don't","i dont","tell me",
    "?","help","עזור","הסבר",
]
GROUNDING_NUDGE_1    = "\U0001f499 אני כאן איתך. מצאת משהו אחד?"
GROUNDING_NUDGE_2    = "\u23f3 נראה שאתה צריך יותר זמן. אני כאן כשתהיה מוכן."
GROUNDING_CHAT_REPLY = "אני כאן רק כדי לעזור לך להתייצב. נסה לציין דברים שאתה {hint} כרגע."
GROUNDING_HINTS      = ["רואה","יכול לגעת בהם","שומע","מריח","יכול לטעום","מרגיש"]
BREATHING_STOP_WORDS = {"לא","ל","no","n","די","stop","done"}
GREET_WORDS          = {"שלום","היי","הי","hello","hi","hey","חזרתי"}
CRISIS_WORDS = [
    "suicide","kill myself","want to die","end my life","cut myself",
    "no reason to live","no hope","worthless",
    "להתאבד","למות","לסיים הכל","להיעלם","רוצה למות","בא לי למות",
    "לחתוך","להפסיק את הסבל","אין טעם","אין תקווה","חסר סיכוי",
    "קצה היכולת","לא יכול יותר","נמאס לי מהכל","אבוד לי",
    "מכתב פרידה","צוואה","סליחה מכולם","הכל נגמר",
    "חושך מוחלט","לישון ולא לקום",
]

# Register allowed messages AFTER they are defined
_register_allowed_messages()

def is_crisis(text):
    return any(w.lower() in text.lower() for w in CRISIS_WORDS)

def is_grounding_chat(text, step):
    t = text.lower().strip()
    if any(phrase in t for phrase in GROUNDING_CHAT_PHRASES):
        return True
    if step < 5 and len(text.split()) < 1:
        return True
    return False

# ══════════════════════════════════════════════════════════════════════════════
# ── BREATHING ─────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def nudge_after_welcome(phone, welcomed_time):
    """60s after welcome → send nudge if user hasn't responded yet."""
    time.sleep(60)
    s = get_state(phone)
    # Only nudge if user still hasn't picked a tool
    if s["tool"] == "none" and s["welcomed"] and s["last_msg_time"] <= welcomed_time + 1:
        send_message(phone, MSG_WELCOME_NUDGE)


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

def run_breathing_round(phone):
    s = get_state(phone)
    my_round_id = s["round_id"]
    for i, part in enumerate(BREATHING_PARTS):
        s = get_state(phone)
        if s["tool"] != "breathing" or s["round_id"] != my_round_id:
            return
        send_message(phone, part)
        if i < len(BREATHING_PARTS) - 1:
            time.sleep(5)
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
# ── MAIN HANDLER ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def handle_message(phone, text):
    text = text.strip()
    t    = text.lower()

    # ג) Rate limit check
    if rate_limit_check(phone):
        print("[GUARDIAN] Rate limit hit: {}".format(phone))
        return

    # Debounce
    s   = get_state(phone)
    now = time.time()
    if now - s["last_msg_time"] < DEBOUNCE_SEC:
        return

    # א) Injection defense — silently drop, log, reset to menu
    if guardian_check_input(text):
        print("[GUARDIAN] Injection attempt from {}: {}".format(phone, text[:80]))
        set_state(phone, tool="none", step=0, force_save=True)
        send_message(phone, MSG_OFF_TOPIC)
        return

    set_state(phone, last_msg_time=now)
    s    = get_state(phone)
    tool = s["tool"]
    step = s["step"]

    # Crisis
    if is_crisis(text):
        send_message(phone, MSG_CRISIS)
        return

    # First message
    if not s["welcomed"]:
        set_state(phone, welcomed=True, force_save=True)
        send_logo(phone)
        send_message(phone, MSG_WELCOME)
        _executor.submit(nudge_after_welcome, phone, now)
        return

    # Breathing
    if tool == "breathing":
        if t in BREATHING_STOP_WORDS:
            set_state(phone, tool="none", step=0,
                      round_id=s["round_id"] + 1, force_save=True)
            send_message(phone, MSG_BREATHING_STOP)
        else:
            new_round = s["round_id"] + 1
            set_state(phone, round_id=new_round, force_save=True)
            _executor.submit(run_breathing_round, phone)
        return

    # Grounding
    if tool == "grounding":
        gs = s["grounding_session"]
        if t in {"חזור","איפוס","reset","back","stop","די"}:
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

    # Routing
    if text == "א" or t == "a":
        new_round = s["round_id"] + 1
        set_state(phone, tool="breathing", step=0,
                  round_id=new_round, force_save=True)
        send_message(phone, BREATHING_START)
        _executor.submit(run_breathing_round, phone)
        return

    if text == "ב" or t == "b":
        new_gs = s["grounding_session"] + 1
        set_state(phone, tool="grounding", step=0,
                  wait_count=0, grounding_session=new_gs, force_save=True)
        send_message(phone, GROUNDING_STEPS[0])
        _executor.submit(nudge_if_silent_grounding, phone, 0, new_gs)
        return

    if t in GREET_WORDS:
        send_message(phone, MSG_RETURNING)
        return

    send_message(phone, MSG_OFF_TOPIC)

# ══════════════════════════════════════════════════════════════════════════════
# ── ADMIN DASHBOARD + API ─────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _check_admin_key(req):
    # Accept key from header OR query param
    return (req.headers.get("X-Admin-Key") == ADMIN_API_KEY or
            req.args.get("key") == ADMIN_API_KEY)

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    if not _check_admin_key(request):
        return '''
        <html><head><title>SafeHarbor Admin</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>
          body{font-family:sans-serif;display:flex;align-items:center;
               justify-content:center;height:100vh;margin:0;background:#f5f5f5}
          .box{background:#fff;padding:32px;border-radius:12px;box-shadow:0 2px 12px #0002;width:300px}
          h2{margin:0 0 20px;font-size:18px;color:#333}
          input{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;
                font-size:15px;box-sizing:border-box;margin-bottom:12px}
          button{width:100%;padding:10px;background:#2563eb;color:#fff;
                 border:none;border-radius:8px;font-size:15px;cursor:pointer}
          button:hover{background:#1d4ed8}
        </style></head>
        <body><div class="box">
          <h2>🔒 SafeHarbor Admin</h2>
          <form method="get">
            <input type="password" name="key" placeholder="Admin key" autofocus>
            <button type="submit">כניסה</button>
          </form>
        </div></body></html>
        ''', 401

    with _blacklist_lock:
        bl_copy = dict(_blacklist)

    rows = ""
    for phone, info in sorted(bl_copy.items(), key=lambda x: x[1].get("time", 0), reverse=True):
        rows += '''
        <tr>
          <td style="font-family:monospace">{phone}</td>
          <td>{reason}</td>
          <td style="color:#888;font-size:13px">{ts}</td>
          <td>
            <button onclick="removePhone('{phone}')" 
                    style="background:#dc2626;color:#fff;border:none;padding:5px 12px;
                           border-radius:6px;cursor:pointer;font-size:13px">
              הסר
            </button>
          </td>
        </tr>'''.format(
            phone=phone,
            reason=info.get("reason", ""),
            ts=info.get("time_str", "")
        )

    if not rows:
        rows = '<tr><td colspan="4" style="text-align:center;color:#888;padding:24px">אין מספרים חסומים</td></tr>'

    key = request.args.get("key", "")
    html = '''
    <html><head><title>SafeHarbor Admin</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      *{{box-sizing:border-box;margin:0;padding:0}}
      body{{font-family:sans-serif;background:#f5f5f5;padding:24px;direction:rtl}}
      h1{{font-size:20px;color:#1e293b;margin-bottom:20px}}
      .card{{background:#fff;border-radius:12px;box-shadow:0 2px 8px #0001;
             padding:20px;margin-bottom:20px}}
      table{{width:100%;border-collapse:collapse;font-size:14px}}
      th{{text-align:right;padding:10px 12px;background:#f8fafc;
          color:#64748b;font-weight:600;border-bottom:2px solid #e2e8f0}}
      td{{padding:10px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}}
      tr:hover td{{background:#fafafa}}
      .add-row{{display:flex;gap:10px;margin-top:12px}}
      .add-row input{{flex:1;padding:9px 12px;border:1px solid #ddd;
                      border-radius:8px;font-size:14px}}
      .add-row button{{padding:9px 18px;background:#2563eb;color:#fff;
                       border:none;border-radius:8px;cursor:pointer;font-size:14px}}
      .add-row button:hover{{background:#1d4ed8}}
      .badge{{background:#fee2e2;color:#dc2626;padding:2px 8px;
              border-radius:999px;font-size:12px;font-weight:600}}
      #msg{{padding:10px;background:#dcfce7;color:#166534;border-radius:8px;
            margin-bottom:16px;display:none;font-size:14px}}
    </style></head>
    <body>
      <h1>🔒 SafeHarbor — ניהול רשימה שחורה</h1>
      <div id="msg"></div>

      <div class="card">
        <table>
          <thead><tr>
            <th>מספר טלפון</th>
            <th>סיבה</th>
            <th>תאריך</th>
            <th></th>
          </tr></thead>
          <tbody id="bl-table">{rows}</tbody>
        </table>

        <div class="add-row">
          <input id="new-phone" type="text" placeholder="הוספה ידנית: 972501234567" dir="ltr">
          <button onclick="addPhone()">חסום</button>
        </div>
      </div>

      <script>
        const KEY = "{key}";
        function showMsg(txt, ok) {{
          const el = document.getElementById("msg");
          el.textContent = txt;
          el.style.display = "block";
          el.style.background = ok ? "#dcfce7" : "#fee2e2";
          el.style.color = ok ? "#166534" : "#dc2626";
          setTimeout(() => {{ el.style.display = "none"; }}, 3000);
        }}
        function removePhone(phone) {{
          if (!confirm("להסיר " + phone + " מהרשימה השחורה?")) return;
          fetch("/admin/blacklist/" + phone + "?key=" + KEY, {{method:"DELETE"}})
            .then(r => r.json()).then(d => {{
              showMsg("הוסר: " + phone, true);
              setTimeout(() => location.reload(), 1000);
            }});
        }}
        function addPhone() {{
          const phone = document.getElementById("new-phone").value.trim();
          if (!phone) return;
          fetch("/admin/blacklist/" + phone + "?key=" + KEY, {{
            method:"POST",
            headers:{{"Content-Type":"application/json"}},
            body: JSON.stringify({{reason:"manual"}})
          }}).then(r => r.json()).then(d => {{
            showMsg("נוסף: " + phone, true);
            setTimeout(() => location.reload(), 1000);
          }});
        }}
      </script>
    </body></html>
    '''.format(rows=rows, key=key)
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
    removed = remove_from_blacklist(phone)
    if removed:
        return jsonify({"status": "removed", "phone": phone}), 200
    return jsonify({"status": "not_found", "phone": phone}), 404

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
                        phone = msg["from"]
                        text  = msg["text"]["body"]
                        _executor.submit(handle_message, phone, text)
    except Exception as e:
        print("[webhook error] {}".format(e))
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health():
    return "SafeHarbor Bot is running", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
