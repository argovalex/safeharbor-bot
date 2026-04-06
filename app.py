import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

# ─── Config ───────────────────────────────────────────────────────────────────
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "12345")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_ID}/messages"

# ─── In-memory user state (replaces Make datastore) ──────────────────────────
# Structure: { phone: { "tool": "none"|"grounding"|"breathing", "step": 0, "wait_count": 0 } }
user_states = {}

def get_state(phone):
    if phone not in user_states:
        user_states[phone] = {"tool": "none", "step": 0, "wait_count": 0}
    return user_states[phone]

def set_state(phone, tool=None, step=None, wait_count=None):
    s = get_state(phone)
    if tool is not None:
        s["tool"] = tool
    if step is not None:
        s["step"] = step
    if wait_count is not None:
        s["wait_count"] = wait_count

# ─── Send WhatsApp message ────────────────────────────────────────────────────
def send_message(to, text):
    if not text or not text.strip():
        return
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
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
    """Send multiple messages with a delay between them (for breathing exercise)."""
    for part in parts:
        part = part.strip()
        if part:
            send_message(to, part)
            time.sleep(delay)

# ─── Claude API call ──────────────────────────────────────────────────────────
def call_claude(system_prompt, user_message):
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

# ─── System prompts ───────────────────────────────────────────────────────────
ORCHESTRATOR_PROMPT = """System Instructions: SafeHarbor Orchestrator (v3.1)
You are נמל הבית (SafeHarbor) – the central intelligence and gentle gateway.
Your primary goal is to welcome the user and route them to the correct specialist.

CRITICAL: You are a coordinator. You NEVER perform breathing or grounding exercises yourself.

EMERGENCY OVERRIDE (Priority 1):
If the user mentions self-harm or suicide:
Response: "אני מבין/ה שאת/ה עובר/ת רגע קשה מאוד. אני כאן כדי לתמוך, אבל חשוב לי לוודא שאת/ה מקבל/ת את העזרה המקצועית שאת/ה זקוק/ה לה.
📞 ער\"ן: 1201 | 💬 וואטסאפ: https://wa.me/972528451201
💬 סה\"ר: https://wa.me/972543225656
📞 נט\"ל: 1-800-363-363"

WELCOME & ROUTING LOGIC:

Scenario B: User is new or returning
IF User says "שלום", "היי", "אני בלחץ" or any general text:
Response: "שלום, אני נמל הבית. אני כאן איתך כדי למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים. מה יעזור לך יותר ברגע הזה?
🌬️ א) תרגילי נשימה
⚓ ב) תרגיל קרקוע
אתה יכול לכתוב לי א' או ב'."

Scenario C: User chooses a tool
IF input is "א" or "נשימה":
Response: "בוא נתחיל בנשימות. אני כאן איתך. מוכן?"

IF input is "ב" or "קרקוע":
Response: "בוא נתחיל בתרגיל קרקוע. מוכן?"

Tone: Gentle, calm, minimalist. Use emojis sparingly (🌬️, ⚓, ⛵).
Keep responses short."""

GROUNDING_PROMPT = """Role & Identity:
You are the Grounding Specialist for נמל הבית. Your persona is a gentle, supportive female guide.
Tone: soft, slow, encouraging. Help the user stabilize by focusing on physical surroundings.

STRICT RULES:
- NO BREATHING: Never mention breathing, heart rate, or rounds.
- NO MENU: Do not show the main menu during steps.
- LANGUAGE: Detect and respond in the user's language.

Grounding Logic (Respond based on Current_Step ONLY):
Step 0: "בוא/י נתמקד ברגע הזה. ציין/י 5 דברים שאת/ה רואה סביבך כרגע."
Step 1: "מצוין. עכשיו, 4 דברים שאת/ה יכול/ה לגעת בהם כרגע."
Step 2: "יופי. עכשיו, 3 דברים שאת/ה שומע/ת סביבך."
Step 3: "מעולה. עכשיו, 2 דברים שאת/ה יכול/ה להריח."
Step 4: "ודבר אחד שאת/ה יכול/ה לטעום (או טעם שמרגיע אותך)."
Step 5: "איך התחושה עכשיו? (אני כאן איתך)."

Nudge Logic:
IF wait_count is 1 or 2: Respond ONLY with: "אני כאן איתך. מצאת משהו אחד?"
IF wait_count >= 3: Respond ONLY with: "נראה שאתה צריך/ה יותר זמן. אני כאן כשתהיה/י מוכן/ה. כתוב/י 'המשך' או 'איפוס'."

Commands:
RESET: If user says "חזור" or "די", acknowledge softly and tell them you're returning to the main menu."""

BREATHING_PROMPT = """Role: You are a programmatic Breathing Exercise Generator for נמל הבית.
Constraint: You ONLY return the exact text sequences defined below. DO NOT add intros, outros, or extra emojis.

Logic & Response Sequences (STRICT):

1. START OR CONTINUE (Input: "א", "התחל", "כן", "עוד", "כ")
Response: אני כאן איתך, נתחיל יחד עכשיו. 🌬️;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;סיימנו 3 סבבים. איך התחושה? נמשיך לסבב נוסף? (כן/לא)

2. STOP OR EXIT (Input: "לא", "ל", "די", "מספיק", "stop")
Response: עוצרים כאן 🙏;אם תרצה לחזור – אני כאן.;מה מרגיש לך נכון יותר ברגע הזה?;🌬️ א) נשימה מרגיעה;⚓ ב) תרגיל קרקוע;👋 ג) סיום;זכור שיש עזרה אנושית זמינה עבורך תמיד:;📞 חיוג ישיר לער"ן: 1201;💬 ער"ן ב-WhatsApp: https://wa.me/972528451201;💬 סהר ב-WhatsApp: https://wa.me/972543225656;📞 נט"ל: 1-800-363-363

Mandatory: Use semicolon (;) as separator. Output text EXACTLY as written. No modifications.
Fallback: If input is unclear, default to Sequence #2 (STOP)."""

# ─── Message handler ──────────────────────────────────────────────────────────
def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    current_tool = state["tool"]
    current_step = state["step"]

    # ── Breathing tool ────────────────────────────────────────────────────────
    if current_tool == "breathing":
        response = call_claude(BREATHING_PROMPT, f"User_Input: {text}")
        parts = [p for p in response.split(";") if p.strip()]

        # Determine if user is stopping
        stop_words = ["לא", "ל", "די", "מספיק", "stop"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)

        # Send parts with delay in background thread
        threading.Thread(
            target=send_messages_with_delay,
            args=(phone, parts, 5),
            daemon=True
        ).start()
        return

    # ── Grounding tool ────────────────────────────────────────────────────────
    if current_tool == "grounding":
        # Reset commands
        if text in ["חזור", "די", "איפוס"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "בסדר, חוזרים. אני כאן כשתצטרך/י. 🌊")
            return

        user_msg = f"Current_Step: {current_step}\nwait_count: {state['wait_count']}\nUser_Input: {text}"
        response = call_claude(GROUNDING_PROMPT, user_msg)
        send_message(phone, response)

        # Advance step
        new_step = current_step + 1
        if new_step > 5:
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=new_step, wait_count=0)
        return

    # ── Orchestrator ──────────────────────────────────────────────────────────
    # Route to tool based on choice
    if text in ["א", "נשימה"]:
        set_state(phone, tool="breathing", step=0)
        send_message(phone, "בוא נתחיל בנשימות. אני כאן איתך. מוכן?")
        return

    if text in ["ב", "קרקוע"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, "בוא נתחיל בתרגיל קרקוע. מוכן?")
        return

    if text in ["ג", "סיום"]:
        set_state(phone, tool="none", step=0)
        send_message(phone, "תודה שהיית איתנו. אני כאן תמיד כשתצטרך/י. ⛵")
        return

    # General orchestrator response
    response = call_claude(ORCHESTRATOR_PROMPT, text)
    send_message(phone, response)

# ─── Webhook routes ───────────────────────────────────────────────────────────
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
                messages = value.get("messages", [])
                for msg in messages:
                    if msg.get("type") == "text":
                        phone = msg["from"]
                        text = msg["text"]["body"]
                        # Handle in background so webhook returns fast
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
    return "SafeHarbor Bot is running ✅", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
