# v17 - Grounding rewrite: input validation, immediate step advance, 60s×2 nudge
import os
import time
import json
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

WHATSAPP_TOKEN    = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "12345")
WHATSAPP_API_URL  = "https://graph.facebook.com/v22.0/{}/messages".format(WHATSAPP_PHONE_ID)

STATE_FILE  = "/tmp/user_states.json"
_state_lock = threading.Lock()

# ── Persistent state ──────────────────────────────────────────────────────────

def load_states():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_states(states):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(states, f, ensure_ascii=False)
    except Exception as e:
        print("[save_states error] {}".format(e))

_all_states = load_states()

def get_state(phone):
    with _state_lock:
        if phone not in _all_states:
            _all_states[phone] = {
                "tool": "none", "step": 0,
                "welcomed": False, "round_id": 0,
                "last_msg_time": 0, "wait_count": 0
            }
        # Ensure wait_count exists in older saved states
        _all_states[phone].setdefault("wait_count", 0)
        return dict(_all_states[phone])   # return a COPY to avoid race conditions

def set_state(phone, **kwargs):
    with _state_lock:
        s = _all_states.setdefault(phone, {
            "tool": "none", "step": 0,
            "welcomed": False, "round_id": 0,
            "last_msg_time": 0, "wait_count": 0
        })
        s.setdefault("wait_count", 0)
        s.update(kwargs)
        save_states(_all_states)

# ── WhatsApp sender ───────────────────────────────────────────────────────────

def send_message(to, text):
    if not text or not text.strip():
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

def send_messages_with_delay(to, parts, delay=5):
    for part in parts:
        p = part.strip()
        if p:
            send_message(to, p)
            time.sleep(delay)

# ── Messages ──────────────────────────────────────────────────────────────────

MSG_WELCOME = (
    'שלום, אני נמל הבית. אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.\n\n'
    'אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:\n'
    '\U0001f4de ע"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\U0001f4de נט"ל: 1-800-363-363\n\n'
    'מה יעזור לך יותר ברגע הזה?\n'
    '\U0001f32c\ufe0f א) תרגילי נשימה\n'
    '\u2693 ב) תרגיל קרקוע'
)

MSG_RETURNING = (
    'היי, טוב שחזרת אלי. \U0001f499\n'
    'אני נמל הבית, ואני כאן איתך שוב.\n\n'
    'מה מרגיש לך נכון יותר ברגע הזה?\n'
    '\U0001f32c\ufe0f א) נשימה מרגיעה\n'
    '\u2693 ב) תרגיל קרקוע\n\n'
    'זכור שיש עזרה אנושית זמינה עבורך תמיד:\n'
    '\U0001f4de ע"ן: 1201 | \U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\U0001f4de נט"ל: 1-800-363-363'
)

MSG_NUDGE = (
    "אני כאן איתך, אתה עדיין איתי? "
    "בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. \u2693"
)

MSG_CRISIS = (
    'אני מבינה שאתה עובר רגע קשה מאוד. אני כאן איתך.\n\n'
    '\U0001f4de ע"ן: 1201\n'
    '\U0001f4ac https://wa.me/972528451201\n'
    '\U0001f4ac סה"ר: https://wa.me/972543225656\n'
    '\U0001f4de נט"ל: 1-800-363-363\n\n'
    'יש מי שרוצה לעזור לך. אנא פנה אליהם. \U0001f499'
)

MSG_OFF_TOPIC = (
    'אני כאן רק כדי לעזור לך להתרגע ולהתייצב. בוא נתמקד במה שמרגיש ברגע זה:\n\n'
    '\U0001f32c\ufe0f א) תרגילי נשימה\n'
    '\u2693 ב) תרגיל קרקוע'
)

MSG_BREATHING_STOP = "אני כאן אם תצטרך אותי שוב. שמור על עצמך. \U0001f499"
MSG_RESET          = "בסדר, אני כאן כשתצטרך. \U0001f30a"
BREATHING_START    = "אני כאן איתך בוא נספור יחד. \U0001f32c\ufe0f"

BREATHING_PARTS = [
    "\U0001f32c\ufe0f שאיפה איטית... 21-22-23-24-25",
    "\u270b עצור... 21-22-23-24-25",
    "\U0001f343 נשיפה איטית... 21-22-23-24-25",
    "\u2693 מנוחה... 21-22-23-24-25",
    "\U0001f32c\ufe0f שאיפה איטית... 21-22-23-24-25",
    "\u270b עצור... 21-22-23-24-25",
    "\U0001f343 נשיפה איטית... 21-22-23-24-25",
    "\u2693 מנוחה... 21-22-23-24-25",
    "\U0001f32c\ufe0f שאיפה איטית... 21-22-23-24-25",
    "\u270b עצור... 21-22-23-24-25",
    "\U0001f343 נשיפה איטית... 21-22-23-24-25",
    "\u2693 מנוחה... 21-22-23-24-25",
    "סיימנו 3 סבבים. איך התחושה? נמשיך? (כן/לא)"
]

GROUNDING_STEPS = [
    "בוא נתמקד ברגע הזה. ציין 5 דברים שאתה רואה סביבך כרגע.",
    "מצוין. עכשיו, 4 דברים שאתה יכול לגעת בהם כרגע.",
    "יופי. עכשיו, 3 דברים שאתה שומע סביבך.",
    "מעולה. עכשיו, 2 דברים שאתה יכול להריח.",
    "ודבר אחד שאתה יכול לטעום (או טעם שמרגיע אותך).",
    "איך התחושה עכשיו?"
]

# Required word count per step (minimum words the user must provide)
GROUNDING_MIN_WORDS = [5, 4, 3, 2, 1, 1]

# Phrases that look like conversation rather than grounding answers
GROUNDING_CHAT_PHRASES = [
    "מה זה", "למה", "אני לא", "אני לא יודע", "לא יודע",
    "מה אתה", "מה את", "לא רוצה", "אני רוצה", "תגיד לי",
    "why", "what", "how", "i don't", "i dont", "tell me",
    "?", "help", "עזור", "הסבר",
]

GROUNDING_NUDGE_1 = "אני כאן איתך. מצאת משהו אחד?"
GROUNDING_NUDGE_2 = "נראה שאתה צריך יותר זמן. אני כאן כשתהיה מוכן."

def is_grounding_chat(text, step):
    """Returns True if the input looks like conversation, not a grounding answer."""
    t = text.lower().strip()
    # Check for chat phrases
    if any(phrase in t for phrase in GROUNDING_CHAT_PHRASES):
        return True
    # Check minimum word count for steps 0-4
    if step < 5:
        word_count = len(text.split())
        if word_count < 1:
            return True
    return False

GROUNDING_CHAT_REPLY = "אני כאן רק כדי לעזור לך להתייצב. נסה לציין דברים שאתה {hint} כרגע."
GROUNDING_HINTS = ["רואה", "יכול לגעת בהם", "שומע", "מריח", "יכול לטעום", "מרגיש"]

# ── Grounding nudge (60s × 2) ─────────────────────────────────────────────────

def nudge_if_silent_grounding(phone, my_step):
    """60s → nudge 1. Another 60s → nudge 2. Stops if step changed."""
    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "grounding" or s["step"] != my_step:
        return
    send_message(phone, GROUNDING_NUDGE_1)

    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "grounding" or s["step"] != my_step:
        return
    send_message(phone, GROUNDING_NUDGE_2)

CRISIS_WORDS = [
    "suicide","kill myself","want to die","end my life","cut myself",
    "no reason to live","no hope","worthless",
    "להתאבד","למות","לסיים הכל","להיעלם","רוצה למות","בא לי למות",
    "לחתוך","להפסיק את הסבל","אין טעם","אין תקווה","חסר סיכוי",
    "קצה היכולת","לא יכול יותר","נמאס לי מהכל","אבוד לי",
    "מכתב פרידה","צוואה","סליחה מכולם","הכל נגמר",
    "חושך מוחלט","לישון ולא לקום",
]

BREATHING_STOP_WORDS = {"לא", "ל", "no", "n", "די", "stop", "done"}
GREET_WORDS          = {"שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי"}

def is_crisis(text):
    return any(w.lower() in text.lower() for w in CRISIS_WORDS)

# ── Breathing threads ─────────────────────────────────────────────────────────

def breathing_post_round_wait(phone, my_round_id):
    """
    Waits after a round ends. Sends nudge at 30s and 60s only if:
    - tool is still 'breathing'  AND
    - round_id hasn't changed (no new round started)
    Both conditions must hold at the moment of checking.
    """
    time.sleep(30)
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return                          # user replied or stopped → abort
    send_message(phone, MSG_NUDGE)

    time.sleep(60)
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return                          # user replied or stopped → abort
    send_message(phone, MSG_NUDGE)

def run_breathing_round(phone):
    s = get_state(phone)
    my_round_id = s["round_id"]

    # Send first 12 parts with 5s delay between them
    # Do NOT sleep after the last message (the question) — user may reply immediately
    for i, part in enumerate(BREATHING_PARTS):
        s = get_state(phone)
        if s["tool"] != "breathing" or s["round_id"] != my_round_id:
            return
        send_message(phone, part)
        if i < len(BREATHING_PARTS) - 1:   # sleep between messages, NOT after the last
            time.sleep(5)

    # Round finished — start watcher only if still in same round
    s = get_state(phone)
    if s["tool"] != "breathing" or s["round_id"] != my_round_id:
        return
    set_state(phone, last_msg_time=time.time())
    threading.Thread(
        target=breathing_post_round_wait,
        args=(phone, my_round_id),
        daemon=True
    ).start()


# ── Main handler ──────────────────────────────────────────────────────────────

def handle_message(phone, text):
    text = text.strip()
    t    = text.lower()

    set_state(phone, last_msg_time=time.time())

    s    = get_state(phone)
    tool = s["tool"]
    step = s["step"]

    # 1. Crisis
    if is_crisis(text):
        send_message(phone, MSG_CRISIS)
        return

    # 2. First message ever
    if not s["welcomed"]:
        set_state(phone, welcomed=True)
        send_message(phone, MSG_WELCOME)
        return

    # 3. Breathing — catches ALL input before anything else
    if tool == "breathing":
        if t in BREATHING_STOP_WORDS:
            # Stop immediately — round_id bump kills any running watcher
            set_state(phone, tool="none", step=0, round_id=s["round_id"] + 1)
            send_message(phone, MSG_BREATHING_STOP)
        else:
            # Any other input = new round
            new_round = s["round_id"] + 1
            set_state(phone, round_id=new_round)
            threading.Thread(target=run_breathing_round, args=(phone,), daemon=True).start()
        return

    # 4. Grounding — catches ALL input before routing
    if tool == "grounding":
        # Exit words
        if t in {"חזור", "איפוס", "reset", "back", "stop", "די"}:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, MSG_RESET)
            return
        # Validate: reject conversation, guide back to task
        if is_grounding_chat(text, step):
            hint = GROUNDING_HINTS[step]
            send_message(phone, GROUNDING_CHAT_REPLY.format(hint=hint))
            return
        # Valid answer → advance immediately to next step
        set_state(phone, wait_count=0)
        next_step = step + 1
        if next_step < len(GROUNDING_STEPS):
            set_state(phone, step=next_step)
            send_message(phone, GROUNDING_STEPS[next_step])
            threading.Thread(
                target=nudge_if_silent_grounding, args=(phone, next_step), daemon=True
            ).start()
        else:
            # Step 5 answered → back to menu
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, MSG_RETURNING)
        return

    # 5. Routing
    if text == "א" or t == "a":
        new_round = s["round_id"] + 1
        set_state(phone, tool="breathing", step=0, round_id=new_round)
        send_message(phone, BREATHING_START)
        threading.Thread(target=run_breathing_round, args=(phone,), daemon=True).start()
        return

    if text == "ב" or t == "b":
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, GROUNDING_STEPS[0])
        threading.Thread(
            target=nudge_if_silent_grounding, args=(phone, 0), daemon=True
        ).start()
        return


    # 6. Greeting
    if t in GREET_WORDS:
        send_message(phone, MSG_RETURNING)
        return

    # 7. Unknown
    send_message(phone, MSG_OFF_TOPIC)

# ── Webhook ───────────────────────────────────────────────────────────────────

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
                        threading.Thread(
                            target=handle_message,
                            args=(phone, text),
                            daemon=True
                        ).start()
    except Exception as e:
        print("[webhook error] {}".format(e))
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health():
    return "SafeHarbor Bot is running", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
