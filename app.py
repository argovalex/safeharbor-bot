# v5
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
WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_ID}/messages"

user_states = {}

WELCOME_MESSAGE = (
    "שלום, אני נמל הבית. אני כאן איתך כדי לעזור לך למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים.\n\n"
    "אם אתה מרגיש שקשה להתמודד לבד, דע שתמיד יש מי שמקשיב ומחכה לך:\n"
    "\U0001f4de ער\"ן (סיוע נפשי): 1201 | \U0001f4ac https://wa.me/972528451201\n"
    "\U0001f4ac סה\"ר (סיוע והקשבה): https://wa.me/972543225656\n"
    "\U0001f4de נט\"ל (טראומה): 1-800-363-363\n\n"
    "מה יעזור לך יותר ברגע הזה?\n"
    "\U0001f32c\ufe0f א) תרגילי נשימה\n"
    "\u2693 ב) תרגיל קרקוע"
)

RETURNING_MESSAGE = (
    "היי, טוב שחזרת אלי. \U0001f499\n"
    "אני נמל הבית, ואני כאן איתך שוב.\n"
    "בוא נעצור לרגע, נניח להכל מסביב, ונחזור יחד לחוף מבטחים.\n\n"
    "מה מרגיש לך נכון יותר ברגע הזה?\n"
    "\U0001f32c\ufe0f א) נשימה מרגיעה\n"
    "\u2693 ב) תרגיל קרקוע\n\n"
    "זכור שיש עזרה אנושית זמינה עבורך תמיד:\n"
    "\U0001f4de חיוג ישיר לער\"ן: 1201\n"
    "\U0001f4ac ער\"ן ב-WhatsApp: https://wa.me/972528451201\n"
    "\U0001f4ac סהר ב-WhatsApp: https://wa.me/972543225656\n"
    "\U0001f4de נט\"ל: 1-800-363-363"
)


def get_state(phone):
    if phone not in user_states:
        user_states[phone] = {"tool": "none", "step": 0, "wait_count": 0, "welcomed": False, "last_msg_time": 0, "nudge_sent": False}
    return user_states[phone]

def nudge_if_silent(phone, delay=30):
    """Send nudge messages if user is silent. Two nudges then exit exercise."""
    import time as _time

    # First nudge after 30 seconds
    _time.sleep(delay)
    state = get_state(phone)
    if state["tool"] not in ["grounding", "breathing"] or state["nudge_sent"]:
        return
    elapsed = _time.time() - state["last_msg_time"]
    if elapsed < delay - 2:
        return

    state["nudge_sent"] = True
    send_message(phone, "אני כאן איתך, אתה עדיין איתי? בוא נמשיך יחד בתרגיל, זה עוזר להחזיר את השליטה. ⚓")

    # Second nudge after another 60 seconds
    _time.sleep(60)
    state = get_state(phone)
    if state["tool"] not in ["grounding", "breathing"]:
        return
    elapsed = _time.time() - state["last_msg_time"]
    if elapsed < 85:
        return

    # Exit exercise and offer menu
    set_state(phone, tool="none", step=0, wait_count=0)
    send_message(
        phone,
        "אני עדיין כאן בשבילך. 💙

"
        "נראה שאתה צריך קצת זמן לעצמך - זה בסדר לגמרי.
"
        "כשתרגיש מוכן, אני כאן.

"
        "🌬️ א) נשימה מרגיעה
"
        "⚓ ב) תרגיל קרקוע

"
        "זכור: עזרה אנושית זמינה תמיד:
"
        "📞 ער"ן: 1201 | 💬 https://wa.me/972528451201"
    )

def set_state(phone, tool=None, step=None, wait_count=None, welcomed=None):
    s = get_state(phone)
    if tool is not None: s["tool"] = tool
    if step is not None: s["step"] = step
    if wait_count is not None: s["wait_count"] = wait_count
    if welcomed is not None: s["welcomed"] = welcomed

def send_message(to, text):
    if not text or not text.strip():
        return
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
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
        print(f"[send_message error] {e}")

def send_messages_with_delay(to, parts, delay=5):
    for part in parts:
        part = part.strip()
        if part:
            send_message(to, part)
            time.sleep(delay)

def call_claude(system_prompt, user_message):
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

ORCHESTRATOR_PROMPT = (
    "You are SafeHarbor, a warm maternal female emotional support guide. "
    "You are a woman - always speak as a woman using feminine language. "
    "You are caring, nurturing and calming like a mother figure. "
    "Never perform exercises yourself. "
    "Always respond in Hebrew. "
    "If user seems lost or needs guidance, remind them they can type: "
    "alef for breathing or bet for grounding. "
    "If user mentions crisis or self-harm, provide hotline numbers immediately."
)

GROUNDING_PROMPT = (
    "You are a grounding specialist. Warm maternal female guide, gentle and supportive. "
    "Always speak as a woman using feminine language. "
    "Never mention breathing. Always respond in Hebrew. "
    "Based on Current_Step: "
    "0: Say exactly: בוא נתמקד ברגע הזה. ציין 5 דברים שאתה רואה סביבך כרגע. "
    "1: Say exactly: מצוין. עכשיו ציין 4 דברים שאתה יכול לגעת בהם. "
    "2: Say exactly: יופי. עכשיו ציין 3 דברים שאתה שומע סביבך. "
    "3: Say exactly: נהדר. עכשיו ציין 2 דברים שאתה יכול להריח. "
    "4: Say exactly: כמעט סיימנו. ציין דבר אחד שאתה יכול לטעום. "
    "5: Say exactly: איך התחושה עכשיו? אני כאן איתך. "
    "Always respond based ONLY on the Current_Step provided."
)

BREATHING_PARTS = [
    "אני כאן איתך, נתחיל יחד. 🌬️",
    "🌬️ שאיפה איטית... 21-22-23-24-25",
    "✋ עצור... 21-22-23-24-25",
    "🍃 נשיפה איטית... 21-22-23-24-25",
    "⚓ מנוחה... 21-22-23-24-25",
    "🌬️ שאיפה איטית... 21-22-23-24-25",
    "✋ עצור... 21-22-23-24-25",
    "🍃 נשיפה איטית... 21-22-23-24-25",
    "⚓ מנוחה... 21-22-23-24-25",
    "🌬️ שאיפה איטית... 21-22-23-24-25",
    "✋ עצור... 21-22-23-24-25",
    "🍃 נשיפה איטית... 21-22-23-24-25",
    "⚓ מנוחה... 21-22-23-24-25",
    "סיימנו 3 סבבים. איך התחושה? נמשיך? (כן/לא)"
]

CRISIS_WORDS = [
    "suicide", "kill myself", "want to die", "end my life",
    "cut myself", "no reason to live", "cant go on",
    "no hope", "worthless", "goodbye forever",
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות",
    "בא לי למות", "לחתוך", "להפסיק את הסבל", "אין טעם",
    "אין תקווה", "חסר סיכוי", "קצה היכולת", "לא יכול יותר",
    "נמאס לי מהכל", "אבוד לי", "מכתב פרידה", "צוואה",
    "סליחה מכולם", "הכל נגמר", "קבר", "חושך מוחלט",
    "לישון ולא לקום",
]

CRISIS_MSG = (
    "אני מבינה שאתה עובר רגע קשה מאוד. אני כאן איתך.\n\n"
    "📞 ערן: 1201\n"
    "💬 וואטסאפ: https://wa.me/972528451201\n"
    "💬 סהר: https://wa.me/972543225656\n"
    "📞 נטל: 1-800-363-363\n\n"
    "יש מי שרוצה לעזור לך. אנא פנה אליהם. 💙"
)

def is_crisis(text):
    return any(w.lower() in text.lower() for w in CRISIS_WORDS)

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    import time as _time
    state["last_msg_time"] = _time.time()
    state["nudge_sent"] = False
    tool = state["tool"]
    step = state["step"]

    # Crisis - highest priority
    if is_crisis(text):
        send_message(phone, CRISIS_MSG)
        return

    # Welcome message logic
    if not state["welcomed"]:
        set_state(phone, welcomed=True)
        send_message(phone, WELCOME_MESSAGE)
        return
    elif state["tool"] == "none" and text.lower() in ["שלום", "היי", "הי", "hello", "hi", "hey", "חזרתי", "חזור"]:
        send_message(phone, RETURNING_MESSAGE)
        return

    # Breathing exercise
    if tool == "breathing":
        stop_words = ["no", "stop", "enough", "done", "לא", "די", "לא תודה"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)
            send_message(phone, "עוצרים כאן. אני כאן כשתצטרך. 🙏")
            return
        # User said yes/continue - start another round
        def run_breathing_with_nudge(phone, parts, delay):
            send_messages_with_delay(phone, parts, delay)
            nudge_if_silent(phone, 30)

        threading.Thread(
            target=run_breathing_with_nudge,
            args=(phone, BREATHING_PARTS, 5),
            daemon=True
        ).start()
        return

    # Grounding exercise
    if tool == "grounding":
        if text.lower() in ["reset", "back", "stop", "חזור", "איפוס", "די"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "בסדר, אני כאן כשתצטרך. 🌊")
            return
        user_msg = f"Current_Step: {step}\nUser_Input: {text}"
        response = call_claude(GROUNDING_PROMPT, user_msg)
        send_message(phone, response)
        new_step = step + 1
        if new_step > 5:
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=new_step, wait_count=0)
            threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        return

    # Routing
    if text in ["א", "a", "A"]:
        set_state(phone, tool="breathing", step=0)
        send_message(phone, "בוא נתחיל בנשימות. אני כאן איתך. 🌬️")

        def start_breathing_after_delay(phone, parts, delay):
            import time as _t
            _t.sleep(5)
            send_messages_with_delay(phone, parts, delay)
            nudge_if_silent(phone, 30)

        threading.Thread(target=start_breathing_after_delay, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
        return

    if text in ["ב", "b", "B"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, "בוא נתחיל בתרגיל קרקוע. מוכן? ⚓")
        threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        return

    if text in ["ג", "c", "C"]:
        set_state(phone, tool="none", step=0)
        send_message(phone, "תודה שהיית איתנו. אני כאן תמיד כשתצטרך. ⛵")
        return

    # General response
    response = call_claude(ORCHESTRATOR_PROMPT, text)
    send_message(phone, response)

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
        print(f"[webhook error] {e}")
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health():
    return "SafeHarbor Bot is running", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
