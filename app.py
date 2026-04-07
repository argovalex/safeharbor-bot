# v6
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
    "\u05e9\u05dc\u05d5\u05dd, \u05d0\u05e0\u05d9 \u05e0\u05de\u05dc \u05d4\u05d1\u05d9\u05ea. "
    "\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da \u05db\u05d3\u05d9 \u05dc\u05e2\u05d6\u05d5\u05e8 "
    "\u05dc\u05da \u05dc\u05de\u05e6\u05d5\u05d0 \u05e7\u05e6\u05ea \u05e9\u05e7\u05d8 "
    "\u05d5\u05dc\u05d4\u05ea\u05d9\u05d9\u05e6\u05d1 \u05d1\u05e8\u05d2\u05e2\u05d9\u05dd "
    "\u05e9\u05de\u05e8\u05d2\u05d9\u05e9\u05d9\u05dd \u05e2\u05de\u05d5\u05e1\u05d9\u05dd \u05d0\u05d5 \u05db\u05d1\u05d3\u05d9\u05dd.\n\n"
    "\u05d0\u05dd \u05d0\u05ea\u05d4 \u05de\u05e8\u05d2\u05d9\u05e9 \u05e9\u05e7\u05e9\u05d4 \u05dc\u05d4\u05ea\u05de\u05d5\u05d3\u05d3 "
    "\u05dc\u05d1\u05d3, \u05d3\u05e2 \u05e9\u05ea\u05de\u05d9\u05d3 \u05d9\u05e9 \u05de\u05d9 \u05e9\u05de\u05e7\u05e9\u05d9\u05d1 "
    "\u05d5\u05de\u05d7\u05db\u05d4 \u05dc\u05da:\n"
    "\U0001f4de \u05e2\u05e8\"\u05df (\u05e1\u05d9\u05d5\u05e2 \u05e0\u05e4\u05e9\u05d9): 1201 | "
    "\U0001f4ac https://wa.me/972528451201\n"
    "\U0001f4ac \u05e1\u05d4\"\u05e8 (\u05e1\u05d9\u05d5\u05e2 \u05d5\u05d4\u05e7\u05e9\u05d1\u05d4): "
    "https://wa.me/972543225656\n"
    "\U0001f4de \u05e0\u05d8\"\u05dc (\u05d8\u05e8\u05d0\u05d5\u05de\u05d4): 1-800-363-363\n\n"
    "\u05de\u05d4 \u05d9\u05e2\u05d6\u05d5\u05e8 \u05dc\u05da \u05d9\u05d5\u05ea\u05e8 \u05d1\u05e8\u05d2\u05e2 \u05d4\u05d6\u05d4?\n"
    "\U0001f32c\ufe0f \u05d0) \u05ea\u05e8\u05d2\u05d9\u05dc\u05d9 \u05e0\u05e9\u05d9\u05de\u05d4\n"
    "\u2693 \u05d1) \u05ea\u05e8\u05d2\u05d9\u05dc \u05e7\u05e8\u05e7\u05d5\u05e2"
)

RETURNING_MESSAGE = (
    "\u05d4\u05d9\u05d9, \u05d8\u05d5\u05d1 \u05e9\u05d7\u05d6\u05e8\u05ea \u05d0\u05dc\u05d9. \U0001f499\n"
    "\u05d0\u05e0\u05d9 \u05e0\u05de\u05dc \u05d4\u05d1\u05d9\u05ea, \u05d5\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da \u05e9\u05d5\u05d1.\n"
    "\u05d1\u05d5\u05d0 \u05e0\u05e2\u05e6\u05d5\u05e8 \u05dc\u05e8\u05d2\u05e2, \u05e0\u05e0\u05d9\u05d7 \u05dc\u05d4\u05db\u05dc "
    "\u05de\u05e1\u05d1\u05d9\u05d1, \u05d5\u05e0\u05d7\u05d6\u05d5\u05e8 \u05d9\u05d7\u05d3 \u05dc\u05d7\u05d5\u05e3 \u05de\u05d1\u05d8\u05d7\u05d9\u05dd.\n\n"
    "\u05de\u05d4 \u05de\u05e8\u05d2\u05d9\u05e9 \u05dc\u05da \u05e0\u05db\u05d5\u05df \u05d9\u05d5\u05ea\u05e8 \u05d1\u05e8\u05d2\u05e2 \u05d4\u05d6\u05d4?\n"
    "\U0001f32c\ufe0f \u05d0) \u05e0\u05e9\u05d9\u05de\u05d4 \u05de\u05e8\u05d2\u05d9\u05e2\u05d4\n"
    "\u2693 \u05d1) \u05ea\u05e8\u05d2\u05d9\u05dc \u05e7\u05e8\u05e7\u05d5\u05e2\n\n"
    "\u05d6\u05db\u05d5\u05e8 \u05e9\u05d9\u05e9 \u05e2\u05d6\u05e8\u05d4 \u05d0\u05e0\u05d5\u05e9\u05d9\u05ea "
    "\u05d6\u05de\u05d9\u05e0\u05d4 \u05e2\u05d1\u05d5\u05e8\u05da \u05ea\u05de\u05d9\u05d3:\n"
    "\U0001f4de \u05d7\u05d9\u05d5\u05d9\u05d2 \u05d9\u05e9\u05d9\u05e8 \u05dc\u05e2\u05e8\"\u05df: 1201\n"
    "\U0001f4ac \u05e2\u05e8\"\u05df \u05d1-WhatsApp: https://wa.me/972528451201\n"
    "\U0001f4ac \u05e1\u05d4\u05e8 \u05d1-WhatsApp: https://wa.me/972543225656\n"
    "\U0001f4de \u05e0\u05d8\"\u05dc: 1-800-363-363"
)

NUDGE_MESSAGE = (
    "\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da, \u05d0\u05ea\u05d4 \u05e2\u05d3\u05d9\u05d9\u05df \u05d0\u05d9\u05ea\u05d9? "
    "\u05d1\u05d5\u05d0 \u05e0\u05de\u05e9\u05d9\u05da \u05d9\u05d7\u05d3 \u05d1\u05ea\u05e8\u05d2\u05d9\u05dc, "
    "\u05d6\u05d4 \u05e2\u05d5\u05d6\u05e8 \u05dc\u05d4\u05d7\u05d6\u05d9\u05e8 \u05d0\u05ea \u05d4\u05e9\u05dc\u05d9\u05d8\u05d4. \u2693"
)

TIMEOUT_MESSAGE = (
    "\u05d0\u05e0\u05d9 \u05e2\u05d3\u05d9\u05d9\u05df \u05db\u05d0\u05df \u05d1\u05e9\u05d1\u05d9\u05dc\u05da. \U0001f499\n\n"
    "\u05e0\u05e8\u05d0\u05d4 \u05e9\u05d0\u05ea\u05d4 \u05e6\u05e8\u05d9\u05da \u05e7\u05e6\u05ea \u05d6\u05de\u05df \u05dc\u05e2\u05e6\u05de\u05da "
    "\u2013 \u05d6\u05d4 \u05d1\u05e1\u05d3\u05e8 \u05dc\u05d2\u05de\u05e8\u05d9.\n"
    "\u05db\u05e9\u05ea\u05e8\u05d2\u05d9\u05e9 \u05de\u05d5\u05db\u05df, \u05d0\u05e0\u05d9 \u05db\u05d0\u05df.\n\n"
    "\U0001f32c\ufe0f \u05d0) \u05e0\u05e9\u05d9\u05de\u05d4 \u05de\u05e8\u05d2\u05d9\u05e2\u05d4\n"
    "\u2693 \u05d1) \u05ea\u05e8\u05d2\u05d9\u05dc \u05e7\u05e8\u05e7\u05d5\u05e2\n\n"
    "\u05d6\u05db\u05d5\u05e8: \u05e2\u05d6\u05e8\u05d4 \u05d0\u05e0\u05d5\u05e9\u05d9\u05ea \u05d6\u05de\u05d9\u05e0\u05d4 \u05ea\u05de\u05d9\u05d3:\n"
    "\U0001f4de \u05e2\u05e8\"\u05df: 1201 | \U0001f4ac https://wa.me/972528451201"
)

def get_state(phone):
    if phone not in user_states:
        user_states[phone] = {
            "tool": "none", "step": 0, "wait_count": 0,
            "welcomed": False, "last_msg_time": 0, "nudge_sent": False
        }
    return user_states[phone]

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

def nudge_if_silent(phone, delay=30):
    time.sleep(delay)
    state = get_state(phone)
    if state["tool"] not in ["grounding", "breathing"] or state["nudge_sent"]:
        return
    if time.time() - state["last_msg_time"] < delay - 2:
        return
    state["nudge_sent"] = True
    send_message(phone, NUDGE_MESSAGE)
    time.sleep(60)
    state = get_state(phone)
    if state["tool"] not in ["grounding", "breathing"]:
        return
    if time.time() - state["last_msg_time"] < 85:
        return
    set_state(phone, tool="none", step=0, wait_count=0)
    send_message(phone, TIMEOUT_MESSAGE)

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
    "Always speak as a woman using feminine language. "
    "Never perform exercises yourself. Always respond in Hebrew. "
    "If user seems lost, remind them they can type alef for breathing or bet for grounding. "
    "If user mentions crisis or self-harm, provide Israeli hotline numbers immediately."
)

GROUNDING_PROMPT = (
    "You are a grounding specialist. Warm maternal female guide. "
    "Always speak as a woman. Never mention breathing. Always respond in Hebrew. "
    "Based on Current_Step respond with EXACTLY these: "
    "0: Say: boa nitkamked barega hazeh. tzayen 5 dvarim she-ata roeh. "
    "1: Say: metzuyan. achshav tzayen 4 dvarim she-ata yachol liggoa bahem. "
    "2: Say: yofi. achshav tzayen 3 dvarim she-ata shomea. "
    "3: Say: nehedar. achshav tzayen 2 dvarim she-ata yachol lehariah. "
    "4: Say: kivat siyamnu. tzayen davar echad she-ata yachol litom. "
    "5: Say: eich hathusa achshav? ani kan itcha. "
    "IMPORTANT: Translate your response to Hebrew naturally. "
    "Always respond based ONLY on the Current_Step."
)

BREATHING_PARTS = [
    "\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da, \u05e0\u05ea\u05d7\u05d9\u05dc \u05d9\u05d7\u05d3. \U0001f32c\ufe0f",
    "\U0001f32c\ufe0f \u05e9\u05d0\u05d9\u05e4\u05d4 \u05d0\u05d9\u05d8\u05d9\u05ea... 21-22-23-24-25",
    "\u270b \u05e2\u05e6\u05d5\u05e8... 21-22-23-24-25",
    "\U0001f343 \u05e0\u05e9\u05d9\u05e4\u05d4 \u05d0\u05d9\u05d8\u05d9\u05ea... 21-22-23-24-25",
    "\u2693 \u05de\u05e0\u05d5\u05d7\u05d4... 21-22-23-24-25",
    "\U0001f32c\ufe0f \u05e9\u05d0\u05d9\u05e4\u05d4 \u05d0\u05d9\u05d8\u05d9\u05ea... 21-22-23-24-25",
    "\u270b \u05e2\u05e6\u05d5\u05e8... 21-22-23-24-25",
    "\U0001f343 \u05e0\u05e9\u05d9\u05e4\u05d4 \u05d0\u05d9\u05d8\u05d9\u05ea... 21-22-23-24-25",
    "\u2693 \u05de\u05e0\u05d5\u05d7\u05d4... 21-22-23-24-25",
    "\U0001f32c\ufe0f \u05e9\u05d0\u05d9\u05e4\u05d4 \u05d0\u05d9\u05d8\u05d9\u05ea... 21-22-23-24-25",
    "\u270b \u05e2\u05e6\u05d5\u05e8... 21-22-23-24-25",
    "\U0001f343 \u05e0\u05e9\u05d9\u05e4\u05d4 \u05d0\u05d9\u05d8\u05d9\u05ea... 21-22-23-24-25",
    "\u2693 \u05de\u05e0\u05d5\u05d7\u05d4... 21-22-23-24-25",
    "\u05e1\u05d9\u05d9\u05de\u05e0\u05d5 3 \u05e1\u05d1\u05d1\u05d9\u05dd. \u05d0\u05d9\u05da \u05d4\u05ea\u05d7\u05d5\u05e9\u05d4? \u05e0\u05de\u05e9\u05d9\u05da? (\u05db\u05df/\u05dc\u05d0)"
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

CRISIS_MSG = (
    "\u05d0\u05e0\u05d9 \u05de\u05d1\u05d9\u05e0\u05d4 \u05e9\u05d0\u05ea\u05d4 \u05e2\u05d5\u05d1\u05e8 \u05e8\u05d2\u05e2 \u05e7\u05e9\u05d4 \u05de\u05d0\u05d5\u05d3. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da.\n\n"
    "\U0001f4de \u05e2\u05e8\"\u05df: 1201\n"
    "\U0001f4ac \u05d5\u05d5\u05d0\u05d8\u05e1\u05d0\u05e4: https://wa.me/972528451201\n"
    "\U0001f4ac \u05e1\u05d4\"\u05e8: https://wa.me/972543225656\n"
    "\U0001f4de \u05e0\u05d8\"\u05dc: 1-800-363-363\n\n"
    "\u05d9\u05e9 \u05de\u05d9 \u05e9\u05e8\u05d5\u05e6\u05d4 \u05dc\u05e2\u05d6\u05d5\u05e8 \u05dc\u05da. \u05d0\u05e0\u05d0 \u05e4\u05e0\u05d4 \u05d0\u05dc\u05d9\u05d4\u05dd. \U0001f499"
)

def is_crisis(text):
    return any(w.lower() in text.lower() for w in CRISIS_WORDS)

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    state["last_msg_time"] = time.time()
    state["nudge_sent"] = False
    tool = state["tool"]
    step = state["step"]

    if is_crisis(text):
        send_message(phone, CRISIS_MSG)
        return

    if not state["welcomed"]:
        set_state(phone, welcomed=True)
        send_message(phone, WELCOME_MESSAGE)
        return

    if tool == "none" and text in ["\u05e9\u05dc\u05d5\u05dd", "\u05d4\u05d9\u05d9", "\u05d4\u05d9", "hello", "hi", "hey", "\u05d7\u05d6\u05e8\u05ea\u05d9", "\u05d7\u05d6\u05d5\u05e8"]:
        send_message(phone, RETURNING_MESSAGE)
        return

    if tool == "breathing":
        stop_words = ["no", "stop", "done", "\u05dc\u05d0", "\u05d3\u05d9"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)
            send_message(phone, "\u05e2\u05d5\u05e6\u05e8\u05d9\u05dd \u05db\u05d0\u05df. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05db\u05e9\u05ea\u05e6\u05d8\u05e8\u05da. \U0001f64f")
            return
        def run_breathing(phone, parts, delay):
            send_messages_with_delay(phone, parts, delay)
            nudge_if_silent(phone, 30)
        threading.Thread(target=run_breathing, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
        return

    if tool == "grounding":
        if text.lower() in ["reset", "back", "stop", "\u05d7\u05d6\u05d5\u05e8", "\u05d0\u05d9\u05e4\u05d5\u05e1", "\u05d3\u05d9"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "\u05d1\u05e1\u05d3\u05e8, \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05db\u05e9\u05ea\u05e6\u05d8\u05e8\u05da. \U0001f30a")
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

    if text in ["\u05d0", "a", "A"]:
        set_state(phone, tool="breathing", step=0)
        send_message(phone, "\u05d1\u05d5\u05d0 \u05e0\u05ea\u05d7\u05d9\u05dc \u05d1\u05e0\u05e9\u05d9\u05de\u05d5\u05ea. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da. \U0001f32c\ufe0f")
        def delayed_start(phone, parts, delay):
            time.sleep(5)
            run_breathing_fn = lambda: (send_messages_with_delay(phone, parts, delay), nudge_if_silent(phone, 30))
            run_breathing_fn()
        threading.Thread(target=delayed_start, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
        return

    if text in ["\u05d1", "b", "B"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, "\u05d1\u05d5\u05d0 \u05e0\u05ea\u05d7\u05d9\u05dc \u05d1\u05ea\u05e8\u05d2\u05d9\u05dc \u05e7\u05e8\u05e7\u05d5\u05e2. \u05de\u05d5\u05db\u05df? \u2693")
        threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        return

    if text in ["\u05d2", "c", "C"]:
        set_state(phone, tool="none", step=0)
        send_message(phone, "\u05ea\u05d5\u05d3\u05d4 \u05e9\u05d4\u05d9\u05d9\u05ea \u05d0\u05d9\u05ea\u05e0\u05d5. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05ea\u05de\u05d9\u05d3 \u05db\u05e9\u05ea\u05e6\u05d8\u05e8\u05da. \u26f5")
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
