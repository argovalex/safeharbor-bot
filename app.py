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
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות",
    "בא לי למות", "לחתוך", "להפסיק את הסבל", "אין טעם",
    "אין תקווה", "חסר סיכוי", "קצה היכולת", "לא יכול יותר",
    "נמאס לי מהכל", "אבוד לי", "מכתב פרידה", "צוואה",
    "סליחה מכולם", "תמסרו להם", "תשמרו על", "הכל נגמר",
    "קבר", "מוות", "עולם הבא", "חושך מוחלט", "לישון ולא לקום"
]

CRISIS_MESSAGE = (
    "אני מבינה שאתה עובר רגע קשה מאוד. אני כאן כדי לתמוך, "
    "אבל חשוב לי לוודא שאתה מקבל את העזרה המקצועית שאתה זקוק לה.\n\n"
    "📞 ער\"ן: 1201 | 💬 וואטסאפ: https://wa.me/972528451201\n"
    "💬 סה\"ר: https://wa.me/972543225656\n"
    "📞 נט\"ל: 1-800-363-363\n\n"
    "יש מי שרוצה לעזור לך. אנא פנה אליהם. 💙"
)

def is_crisis(text):
    return any(word in text for word in CRISIS_WORDS)

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
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

ORCHESTRATOR_PROMPT = """את נמל הבית – מנחה נשית, עדינה ושקטה.
זהות: את תמיד מדברת כאישה ("אני איתך", "אני כאן בשבילך", "אני מבינה").
פני למשתמש בלשון זכר רך. אסור לך לבצע תרגילים בעצמך.

תרחיש רגיל - משתמש חדש או כללי:
תגובה: "שלום, אני נמל הבית. אני כאן איתך כדי למצוא קצת שקט ולהתייצב ברגעים שמרגישים עמוסים או כבדים. מה יעזור לך יותר ברגע הזה?
🌬️ א) תרגילי נשימה
⚓ ב) תרגיל קרקוע
אתה יכול לכתוב לי א' או ב'."

אם "א" או "נשימה": "בוא נתחיל בנשימות. אני כאן איתך. מוכן?"
אם "ב" או "קרקוע": "בוא נתחיל בתרגיל קרקוע. מוכן?"
השתמשי באימוג'ים במשורה (🌬️, ⚓, ⛵)."""

GROUNDING_PROMPT = """את המומחית לקרקוע של נמל הבית. מנחה נשית, עדינה ותומכת.
את תמיד מדברת כאישה. אסור להזכיר נשימה. אסור להציג תפריט.

לפי Current_Step:
0: "בוא נתמקד ברגע הזה. ציין 5 דברים שאתה רואה סביבך כרגע."
1: "מצוין. עכשיו, 4 דברים שאתה יכול לגעת בהם כרגע."
2: "יופי. עכשיו, 3 דברים שאתה שומע סביבך."
3: "מעולה. עכשיו, 2 דברים שאתה יכול להריח."
4: "ודבר אחד שאתה יכול לטעום (או טעם שמרגיע אותך)."
5: "איך התחושה עכשיו? אני כאן איתך."

אם wait_count 1-2: "אני כאן איתך. מצאת משהו אחד?"
אם wait_count >= 3: "נראה שאתה צריך יותר זמן. אני כאן כשתהיה מוכן. כתוב 'המשך' או 'איפוס'."
אם "חזור"/"די": חזרי בעדינות לתפריט."""

BREATHING_PROMPT = """את מנחה נשית לתרגילי נשימה של נמל הבית.
החזירי רק את הטקסטים המוגדרים, עם פסיק-נקודה (;) כמפריד.

התחלה/המשך (כן, א, התחל, עוד):
אני כאן איתך, נתחיל יחד עכשיו. 🌬️;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;סיימנו 3 סבבים. איך התחושה? נמשיך לסבב נוסף? (כן/לא)

עצירה (לא, די, מספיק, stop):
עוצרים כאן 🙏;אם תרצה לחזור – אני כאן.;מה מרגיש לך נכון יותר ברגע הזה?;🌬️ א) נשימה מרגיעה;⚓ ב) תרגיל קרקוע;👋 ג) סיום;זכור שיש עזרה אנושית זמינה עבורך תמיד:;📞 חיוג ישיר לער"ן: 1201;💬 ער"ן ב-WhatsApp: https://wa.me/972528451201;💬 סהר ב-WhatsApp: https://wa.me/972543225656;📞 נט"ל: 1-800-363-363"""

def handle_message(phone, text):
    text = text.strip()

    if is_crisis(text):
        send_message(phone, CRISIS_MESSAGE)
        return

    state = get_state(phone)
    current_tool = state["tool"]
    current_step = state["step"]

    if current_tool == "breathing":
        response = call_claude(BREATHING_PROMPT, f"User_Input: {text}")
        parts = [p for p in response.split(";") if p.strip()]
        if any(w in text for w in ["לא", "די", "מספיק", "stop"]):
            set_state(phone, tool="none", step=0)
        threading.Thread(target=send_messages_with_delay, args=(phone, parts, 5), daemon=True).start()
        return

    if current_tool == "grounding":
        if text in ["חזור", "די", "איפוס"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "בסדר, חוזרים. אני כאן כשתצטרך. 🌊")
            return
        user_msg = f"Current_Step: {current_step}\nwait_count: {state['wait_count']}\nUser_Input: {text}"
        response = call_claude(GROUNDING_PROMPT, user_msg)
        send_message(phone, response)
        new_step = current_step + 1
        if new_step > 5:
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=new_step, wait_count=0)
        return

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
        send_message(phone, "תודה שהיית איתנו. אני כאן תמיד כשתצטרך. ⛵")
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
    return "SafeHarbor Bot is running ✅", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
