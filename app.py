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

def get_state(phone):
    if phone not in user_states:
        user_states[phone] = {"tool": "none", "step": 0, "wait_count": 0}
    return user_states[phone]

def set_state(phone, tool=None, step=None, wait_count=None):
    s = get_state(phone)
    if tool is not None: s["tool"] = tool
    if step is not None: s["step"] = step
    if wait_count is not None: s["wait_count"] = wait_count

CRISIS_WORDS = [
    "suicide", "kill myself", "want to die", "end my life", "no hope",
    "cut myself", "cant go on", "goodbye forever", "worthless",
]

CRISIS_MESSAGE_HE = (
    "אני מבינה שאתה עובר רגע קשה מאוד. אני כאן כדי לתמוך.\n\n"
    "ערן: 1201\n"
    "https://wa.me/972528451201\n"
    "https://wa.me/972543225656\n"
    "נטל: 1-800-363-363\n\n"
    "יש מי שרוצה לעזור לך. אנא פנה אליהם."
)

CRISIS_MESSAGE_EN = (
    "I understand you are going through a very difficult moment.\n\n"
    "Crisis line: 988 (US) | 116 123 (UK)\n"
    "Text HOME to 741741\n"
    "https://findahelpline.com\n\n"
    "There are people who want to help you. Please reach out."
)

def is_crisis(text):
    return any(word.lower() in text.lower() for word in CRISIS_WORDS)

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
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

ORCHESTRATOR_PROMPT = (
    "You are SafeHarbor, a calm gentle female emotional support guide. "
    "Always speak as a woman. Never do exercises yourself. "
    "Respond in the same language the user writes in. "
    "Greet warmly and ask: would you prefer A) Breathing or B) Grounding? "
    "If Hebrew: use Hebrew naturally. If English: use English."
)

GROUNDING_PROMPT = (
    "You are a grounding specialist. Female guide, gentle and supportive. "
    "Never mention breathing. Respond in same language as user. "
    "Step 0: ask for 5 things they see. "
    "Step 1: 4 things to touch. "
    "Step 2: 3 things to hear. "
    "Step 3: 2 things to smell. "
    "Step 4: 1 thing to taste. "
    "Step 5: ask how they feel now. "
    "Always respond based on Current_Step provided."
)

BREATHING_HE = [
    "אני כאן איתך, נתחיל יחד.",
    "שאיפה... 1-2-3-4-5",
    "עצור... 1-2-3-4-5",
    "נשיפה... 1-2-3-4-5",
    "מנוחה... 1-2-3-4-5",
    "שאיפה... 1-2-3-4-5",
    "עצור... 1-2-3-4-5",
    "נשיפה... 1-2-3-4-5",
    "מנוחה... 1-2-3-4-5",
    "שאיפה... 1-2-3-4-5",
    "עצור... 1-2-3-4-5",
    "נשיפה... 1-2-3-4-5",
    "מנוחה... 1-2-3-4-5",
    "סיימנו 3 סבבים. איך התחושה? נמשיך? (כן/לא)"
]

BREATHING_EN = [
    "I am here with you, let us begin.",
    "Breathe in... 1-2-3-4-5",
    "Hold... 1-2-3-4-5",
    "Breathe out... 1-2-3-4-5",
    "Rest... 1-2-3-4-5",
    "Breathe in... 1-2-3-4-5",
    "Hold... 1-2-3-4-5",
    "Breathe out... 1-2-3-4-5",
    "Rest... 1-2-3-4-5",
    "Breathe in... 1-2-3-4-5",
    "Hold... 1-2-3-4-5",
    "Breathe out... 1-2-3-4-5",
    "Rest... 1-2-3-4-5",
    "3 rounds done. How do you feel? Continue? (yes/no)"
]

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    tool = state["tool"]
    step = state["step"]

    # Detect Hebrew
    is_hebrew = any('\u05d0' <= c <= '\u05ea' for c in text)

    # Crisis check - English words only to avoid encoding
    if is_crisis(text):
        msg = CRISIS_MESSAGE_HE if is_hebrew else CRISIS_MESSAGE_EN
        send_message(phone, msg)
        return

    # Breathing
    if tool == "breathing":
        stop_words = ["no", "stop", "enough", "done"]
        if any(w in text.lower() for w in stop_words) or text in ["לא", "די"]:
            set_state(phone, tool="none", step=0)
            send_message(phone, "עוצרים. אני כאן כשתצטרך." if is_hebrew else "Stopping. I am here when you need me.")
            return
        parts = BREATHING_HE if is_hebrew else BREATHING_EN
        threading.Thread(target=send_messages_with_delay, args=(phone, parts, 5), daemon=True).start()
        return

    # Grounding
    if tool == "grounding":
        if text in ["חזור", "איפוס", "reset", "back", "stop"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "חוזרים. אני כאן." if is_hebrew else "OK, I am here.")
            return
        user_msg = f"Current_Step: {step}\nUser_Input: {text}"
        response = call_claude(GROUNDING_PROMPT, user_msg)
        send_message(phone, response)
        new_step = step + 1
        if new_step > 5:
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=new_step, wait_count=0)
        return

    # Routing
    if text in ["א", "a", "A"]:
        set_state(phone, tool="breathing", step=0)
        send_message(phone, "מוכן? בוא נתחיל." if is_hebrew else "Ready? Let us begin.")
        return

    if text in ["ב", "b", "B"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, "בוא נתחיל בקרקוע." if is_hebrew else "Let us begin grounding.")
        return

    if text in ["ג", "c", "C"]:
        set_state(phone, tool="none", step=0)
        send_message(phone, "תודה. אני כאן תמיד." if is_hebrew else "Thank you. I am always here.")
        return

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
                        threading.Thread(target=handle_message, args=(phone, text), daemon=True).start()
    except Exception as e:
        print(f"[webhook error] {e}")
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health():
    return "SafeHarbor Bot is running", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
