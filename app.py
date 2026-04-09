# v11 - Breathing: clean logic, no off-topic interference, correct nudge/stop
import os
import time
import threading
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "12345")
WHATSAPP_API_URL = "https://graph.facebook.com/v22.0/{}/messages".format(WHATSAPP_PHONE_ID)

user_states = {}

def get_state(phone):
    if phone not in user_states:
        user_states[phone] = {
            "tool": "none", "step": 0, "wait_count": 0,
            "welcomed": False, "last_msg_time": 0, "nudge_sent": False,
            "round_id": 0
        }
    return user_states[phone]

def set_state(phone, **kwargs):
    s = get_state(phone)
    for k, v in kwargs.items():
        s[k] = v

def send_message(to, text):
    if not text or not text.strip():
        return
    headers = {"Authorization": "Bearer {}".format(WHATSAPP_TOKEN), "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text.strip()}
    }
    try:
        r = requests.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print("[send_message error] {}".format(e))

def send_messages_with_delay(to, parts, delay=5):
    for part in parts:
        part = part.strip()
        if part:
            send_message(to, part)
            time.sleep(delay)

# ── Messages ──────────────────────────────────────────────────────────────────

MSG_WELCOME = """שלום, אני נמל הבית. אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.

אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:
\U0001f4de ע"ן: 1201 | \U0001f4ac https://wa.me/972528451201
\U0001f4ac סה"ר: https://wa.me/972543225656
\U0001f4de נט"ל: 1-800-363-363

מה יעזור לך יותר ברגע הזה?
\U0001f32c\ufe0f א) תרגילי נשימה
\u2693 ב) תרגיל קרקוע"""

MSG_RETURNING = """היי, טוב שחזרת אלי. \U0001f499
אני נמל הבית, ואני כאן איתך שוב.
בוא נעצור לרגע, נניח להכל מסביב, ונחזור יחד לחוף מבטחים.

מה מרגיש לך נכון יותר ברגע הזה?
\U0001f32c\ufe0f א) נשימה מרגיעה
\u2693 ב) תרגיל קרקוע

זכור שיש עזרה אנושית זמינה עבורך תמיד:
\U0001f4de ע"ן: 1201 | \U0001f4ac https://wa.me/972528451201
\U0001f4ac סה"ר: https://wa.me/972543225656
\U0001f4de נט"ל: 1-800-363-363"""

MSG_NUDGE = "אני כאן איתך, אתה עדיין איתי? בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. \u2693"

MSG_CRISIS = """אני מבינה שאתה עובר רגע קשה מאוד. אני כאן איתך.

\U0001f4de ע"ן: 1201
\U0001f4ac https://wa.me/972528451201
\U0001f4ac סה"ר: https://wa.me/972543225656
\U0001f4de נט"ל: 1-800-363-363

יש מי שרוצה לעזור לך. אנא פנה אליהם. \U0001f499"""

MSG_OFF_TOPIC = """אני כאן רק כדי לעזור לך להתרגע ולהתייצב. בוא נתמקד במה שמרגיש ברגע זה:

\U0001f32c\ufe0f א) תרגילי נשימה
\u2693 ב) תרגיל קרקוע"""

MSG_BREATHING_STOP = "אני כאן אם תצטרך אותי שוב. שמור על עצמך. \U0001f499"
MSG_RESET = "בסדר, אני כאן כשתצטרך. \U0001f30a"
MSG_END = "תודה שהיית איתנו. אני כאן תמיד כשתצטרך. \u26f5"

BREATHING_START_MSG = "אני כאן איתך בוא נספור יחד. \U0001f32c\ufe0f"

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
    "מצוין. עכשיו ציין 4 דברים שאתה יכול לגעת בהם.",
    "יופי. עכשיו ציין 3 דברים שאתה שומע סביבך.",
    "נהדר. עכשיו ציין 2 דברים שאתה יכול להריח.",
    "כמעט סיימנו. ציין דבר אחד שאתה יכול לטעום.",
    "איך התחושה עכשיו? אני כאן איתך."
]

CRISIS_WORDS = [
    "suicide", "kill myself", "want to die", "end my life",
    "cut myself", "no reason to live", "no hope", "worthless",
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות",
    "בא לי למות", "לחתוך", "להפסיק את הסבל", "אין טעם",
    "אין תקווה", "חסר סיכוי", "קצה היכולת", "לא יכול יותר",
    "נמאס לי מהכל", "אבוד לי", "מכתב פרידה", "צוואה",
    "סליחה מכולם", "הכל נגמר", "חושך מוחלט", "לישון ולא לקום",
]

OFF_TOPIC_WORDS = [
    "who are you", "what are you", "your name", "are you human", "are you ai",
    "who made you", "weather", "news", "politics", "sport", "recipe",
    "phone number", "address", "email", "password", "credit card",
    "user", "users", "data", "database", "information about",
    "מי אתה", "מה אתה", "ספר לי", "משתמשים", "נתונים", "פרטים",
    "מאין אתה", "איפה אתה",
]

BREATHING_STOP_WORDS = ["לא", "ל", "no", "n", "די", "stop", "done"]

def is_crisis(text):
    return any(w.lower() in text.lower() for w in CRISIS_WORDS)

def is_off_topic(text):
    return any(w.lower() in text.lower() for w in OFF_TOPIC_WORDS)

# ── Breathing post-round watcher ──────────────────────────────────────────────

def breathing_post_round_wait(phone, my_round_id):
    """
    After round ends:
    - 30s silence → send MSG_NUDGE
    - 60s more silence → send MSG_NUDGE again
    Aborts silently if round_id changes (new round started or tool changed).
    """
    time.sleep(30)
    state = get_state(phone)
    if state["tool"] != "breathing" or state["round_id"] != my_round_id:
        return
    send_message(phone, MSG_NUDGE)

    time.sleep(60)
    state = get_state(phone)
    if state["tool"] != "breathing" or state["round_id"] != my_round_id:
        return
    send_message(phone, MSG_NUDGE)

def run_breathing_round(phone):
    """Send 12 breathing steps + question, then launch post-round watcher."""
    state = get_state(phone)
    my_round_id = state["round_id"]

    send_messages_with_delay(phone, BREATHING_PARTS, 5)

    # If round_id changed while sending, a new round is already running — stop here
    state = get_state(phone)
    if state["tool"] != "breathing" or state["round_id"] != my_round_id:
        return

    state["last_msg_time"] = time.time()
    state["nudge_sent"] = False
    threading.Thread(
        target=breathing_post_round_wait,
        args=(phone, my_round_id),
        daemon=True
    ).start()

# ── Grounding nudge ───────────────────────────────────────────────────────────

def nudge_if_silent(phone, delay=30):
    time.sleep(delay)
    state = get_state(phone)
    if state["tool"] != "grounding" or state["nudge_sent"]:
        return
    if time.time() - state["last_msg_time"] < delay - 2:
        return
    state["nudge_sent"] = True
    send_message(phone, MSG_NUDGE)

# ── Main message handler ──────────────────────────────────────────────────────

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    state["last_msg_time"] = time.time()
    state["nudge_sent"] = False
    tool = state["tool"]
    step = state["step"]

    # 1. Crisis — always first
    if is_crisis(text):
        send_message(phone, MSG_CRISIS)
        return

    # 2. First time
    if not state["welcomed"]:
        set_state(phone, welcomed=True)
        send_message(phone, MSG_WELCOME)
        return

    # 3. Breathing — intercept ALL input before any other logic
    if tool == "breathing":
        t = text.lower()
        if t in BREATHING_STOP_WORDS:
            set_state(phone, tool="none", step=0, round_id=0)
            send_message(phone, MSG_BREATHING_STOP)
        else:
            # Any other response = continue with a new round
            state["round_id"] = state.get("round_id", 0) + 1
            threading.Thread(target=run_breathing_round, args=(phone,), daemon=True).start()
        return

    # 4. Grounding
    if tool == "grounding":
        if text.lower() in ["חזור", "איפוס", "די", "reset", "back", "stop"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, MSG_RESET)
            return
        next_step = step + 1
        if next_step < len(GROUNDING_STEPS):
            send_message(phone, GROUNDING_STEPS[next_step])
            set_state(phone, step=next_step)
            threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        else:
            set_state(phone, tool="none", step=0, wait_count=0)
        return

    # 5. Routing (tool == "none")
    if text == "א" or text.lower() == "a":
        new_round = state.get("round_id", 0) + 1
        set_state(phone, tool="breathing", step=0, round_id=new_round)
        send_message(phone, BREATHING_START_MSG)
        threading.Thread(target=run_breathing_round, args=(phone,), daemon=True).start()
        return

    if text == "ב" or text.lower() == "b":
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, GROUNDING_STEPS[0])
        threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        return

    if text == "ג" or text.lower() == "c":
        set_state(phone, tool="none", step=0)
        send_message(phone, MSG_END)
        return

    # 6. Returning greeting
    if text.lower() in ["שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי"]:
        send_message(phone, MSG_RETURNING)
        return

    # 7. Off-topic / unknown
    send_message(phone, MSG_OFF_TOPIC)

# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
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
                        text = msg["text"]["body"]
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
