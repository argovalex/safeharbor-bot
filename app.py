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
        model="claude-3-haiku-20240307",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

ORCHESTRATOR_PROMPT = "You are SafeHarbor, a calm gentle female emotional support guide. Always speak as a woman. Never do exercises yourself. Respond in the same language the user writes in. Greet warmly and offer: A) Breathing exercises or B) Grounding exercise. Ask them to type A or B."

GROUNDING_PROMPT = "You are a grounding specialist. Female guide, gentle. Never mention breathing. Respond in same language as user. Step 0: ask for 5 things they see. Step 1: 4 things to touch. Step 2: 3 things to hear. Step 3: 2 things to smell. Step 4: 1 thing to taste. Step 5: ask how they feel now. Always respond based on Current_Step provided."

BREATHING_PARTS = [
    "I am here with you, let us begin.",
    "Breathe in slowly... 1-2-3-4-5",
    "Hold... 1-2-3-4-5",
    "Breathe out slowly... 1-2-3-4-5",
    "Rest... 1-2-3-4-5",
    "Breathe in slowly... 1-2-3-4-5",
    "Hold... 1-2-3-4-5",
    "Breathe out slowly... 1-2-3-4-5",
    "Rest... 1-2-3-4-5",
    "Breathe in slowly... 1-2-3-4-5",
    "Hold... 1-2-3-4-5",
    "Breathe out slowly... 1-2-3-4-5",
    "Rest... 1-2-3-4-5",
    "3 rounds done. How do you feel? Continue? (yes/no)"
]

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    tool = state["tool"]
    step = state["step"]

    if tool == "breathing":
        stop_words = ["no", "stop", "enough", "done", "לא", "די"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)
            send_message(phone, "Stopping. I am here when you need me.")
            return
        threading.Thread(target=send_messages_with_delay, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
        return

    if tool == "grounding":
        if text.lower() in ["reset", "back", "stop", "חזור", "איפוס"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "OK, I am here.")
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

    if text.lower() in ["א", "a"]:
        set_state(phone, tool="breathing", step=0)
        send_message(phone, "Ready? Let us begin breathing together.")
        return

    if text.lower() in ["ב", "b"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, "Let us begin the grounding exercise. Ready?")
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
