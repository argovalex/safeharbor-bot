# -*- coding: utf-8 -*-
import os
import sys
import time
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "12345")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_ID}/messages"

user_states = {}

def get_state(phone):
    if phone not in user_states:
        user_states[phone] = {"tool": "none", "step": 0, "wait_count": 0, "lang": None}
    return user_states[phone]

def set_state(phone, tool=None, step=None, wait_count=None, lang=None):
    s = get_state(phone)
    if tool is not None: s["tool"] = tool
    if step is not None: s["step"] = step
    if wait_count is not None: s["wait_count"] = wait_count
    if lang is not None: s["lang"] = lang

CRISIS_WORDS = [
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות",
    "בא לי למות", "לחתוך", "להפסיק את הסבל", "אין טעם",
    "אין תקווה", "חסר סיכוי", "קצה היכולת", "לא יכול יותר",
    "נמאס לי מהכל", "אבוד לי", "מכתב פרידה", "צוואה",
    "סליחה מכולם", "תמסרו להם", "תשמרו על", "הכל נגמר",
    "קבר", "מוות", "עולם הבא", "חושך מוחלט", "לישון ולא לקום",
    "kill myself", "end my life", "want to die", "suicide",
    "cut myself", "no reason to live", "cant go on",
    "goodbye forever", "no hope", "worthless",
]

def is_crisis(text):
    return any(word.lower() in text.lower() for word in CRISIS_WORDS)

def get_crisis_message(lang):
    if lang == "he":
        return (
            "אני מבינה שאתה עובר רגע קשה מאוד. אני כאן כדי לתמוך, "
            "אבל חשוב לי לוודא שאתה מקבל את העזרה המקצועית שאתה זקוק לה.\n\n"
            "ער\"ן: 1201\n"
            "וואטסאפ: https://wa.me/972528451201\n"
            "סה\"ר: https://wa.me/972543225656\n"
            "נט\"ל: 1-800-363-363\n\n"
            "יש מי שרוצה לעזור לך. אנא פנה אליהם. 💙"
        )
    return (
        "I understand you're going through a very difficult moment. "
        "I'm here to support you, but please reach out for professional help.\n\n"
        "Crisis line: 988 (US) | 116 123 (UK)\n"
        "Crisis Text Line: Text HOME to 741741\n"
        "https://findahelpline.com\n\n"
        "There are people who want to help you. Please reach out. 💙"
    )

def send_message(to, text):
    import json as _json, urllib.request as _urllib
    if not text or not text.strip():
        return
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text.strip()}
    }
    try:
        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urllib.Request(WHATSAPP_API_URL, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {WHATSAPP_TOKEN}")
        req.add_header("Content-Type", "application/json")
        with _urllib.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        print(f"[send_message error] {e}", flush=True)

def send_messages_with_delay(to, parts, delay=5):
    for part in parts:
        part = part.strip()
        if part:
            send_message(to, part)
            time.sleep(delay)

def call_claude(system_prompt, user_message):
    import json as _json
    import urllib.request as _urllib
    payload = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}]
    }
    try:
        body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urllib.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            method="POST"
        )
        req.add_header("x-api-key", ANTHROPIC_API_KEY)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("content-type", "application/json")
        with _urllib.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]
    except Exception as e:
        print(f"[claude error] {e}", flush=True)
        return ""

def detect_language(text):
    try:
        result = call_claude(
            "You detect languages. Reply with ONLY a 2-letter ISO code: he, en, ar, ru, es, fr, de, it, pt. Nothing else.",
            f"What language is this: {text}"
        )
        lang = result.strip().lower()[:2]
        return lang if lang in ["he", "en", "ar", "ru", "es", "fr", "de", "it", "pt"] else "en"
    except:
        return "en"

ORCHESTRATOR_PROMPT_HE = """את נמל הבית - מנחה נשית, עדינה ושקטה.
תמיד מדברת כאישה. פני למשתמש בלשון זכר רך.
אסור לך לבצע תרגילים בעצמך.

ברכי את המשתמש והצעי:
שלום, אני נמל הבית. אני כאן איתך כדי למצוא קצת שקט. מה יעזור לך יותר ברגע הזה?
א) תרגילי נשימה
ב) תרגיל קרקוע
כתוב לי א או ב."""

ORCHESTRATOR_PROMPT_EN = """You are SafeHarbor - a calm, gentle female guide for emotional support.
Always speak as a woman. Never perform exercises yourself.
Always respond in the SAME language the user wrote in.

Welcome the user warmly and offer:
A) Breathing exercises
B) Grounding exercise
Ask them to type A or B."""

GROUNDING_PROMPT_HE = """את המומחית לקרקוע של נמל הבית. מנחה נשית, עדינה.
תמיד מדברי כאישה. אסור להזכיר נשימה. אסור להציג תפריט.

לפי Current_Step:
0: בוא נתמקד ברגע הזה. ציין 5 דברים שאתה רואה סביבך.
1: מצוין. עכשיו 4 דברים שאתה יכול לגעת בהם.
2: יופי. עכשיו 3 דברים שאתה שומע.
3: מעולה. עכשיו 2 דברים שאתה יכול להריח.
4: ודבר אחד שאתה יכול לטעום.
5: איך התחושה עכשיו? אני כאן איתך.

אם wait_count 1-2: אני כאן איתך. מצאת משהו אחד?
אם wait_count >= 3: אני כאן כשתהיה מוכן. כתוב המשך או איפוס."""

GROUNDING_PROMPT_EN = """You are the grounding specialist of SafeHarbor. Female guide, gentle.
Always speak as a woman. Never mention breathing. Never show menu.
Always respond in the SAME language as User_Input.

Based on Current_Step:
0: Name 5 things you can see around you.
1: Name 4 things you can touch right now.
2: Name 3 things you can hear.
3: Name 2 things you can smell.
4: Name 1 thing you can taste.
5: How do you feel now? I am here with you.

wait_count 1-2: Gently encourage, ask if they found one thing.
wait_count >= 3: Say you are here when ready, suggest typing continue or reset."""

BREATHING_PARTS_HE = [
    "אני כאן איתך, נתחיל יחד עכשיו.",
    "שאיפה איטית... 1-2-3-4-5",
    "עצור... 1-2-3-4-5",
    "נשיפה איטית... 1-2-3-4-5",
    "מנוחה... 1-2-3-4-5",
    "שאיפה איטית... 1-2-3-4-5",
    "עצור... 1-2-3-4-5",
    "נשיפה איטית... 1-2-3-4-5",
    "מנוחה... 1-2-3-4-5",
    "שאיפה איטית... 1-2-3-4-5",
    "עצור... 1-2-3-4-5",
    "נשיפה איטית... 1-2-3-4-5",
    "מנוחה... 1-2-3-4-5",
    "סיימנו 3 סבבים. איך התחושה? נמשיך? (כן/לא)"
]

BREATHING_PARTS_EN = [
    "I am here with you, let us begin together.",
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
    "We completed 3 rounds. How do you feel? Continue? (yes/no)"
]

BREATHING_STOP_HE = [
    "עוצרים כאן.",
    "אם תרצה לחזור - אני כאן.",
    "מה מרגיש לך נכון יותר ברגע הזה?",
    "א) נשימה | ב) קרקוע | ג) סיום",
    "עזרה אנושית זמינה תמיד: ערן 1201 | https://wa.me/972528451201"
]

BREATHING_STOP_EN = [
    "Stopping here.",
    "I am here if you want to return.",
    "What feels right for you now?",
    "A) Breathing | B) Grounding | C) End",
    "Human support is always available: https://findahelpline.com"
]

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)

    if state["lang"] is None:
        lang = detect_language(text)
        set_state(phone, lang=lang)
    else:
        lang = state["lang"]

    if is_crisis(text):
        send_message(phone, get_crisis_message(lang))
        return

    current_tool = state["tool"]
    current_step = state["step"]

    # Breathing
    if current_tool == "breathing":
        stop_words = ["לא", "די", "מספיק", "no", "stop", "enough", "done"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)
            parts = BREATHING_STOP_HE if lang == "he" else BREATHING_STOP_EN
        else:
            parts = BREATHING_PARTS_HE if lang == "he" else BREATHING_PARTS_EN
        threading.Thread(target=send_messages_with_delay, args=(phone, parts, 5), daemon=True).start()
        return

    # Grounding
    if current_tool == "grounding":
        reset_words = ["חזור", "די", "איפוס", "back", "stop", "reset"]
        if any(w in text.lower() for w in reset_words):
            set_state(phone, tool="none", step=0, wait_count=0)
            msg = "בסדר, חוזרים. אני כאן כשתצטרך." if lang == "he" else "OK, I am here when you need me."
            send_message(phone, msg)
            return
        prompt = GROUNDING_PROMPT_HE if lang == "he" else GROUNDING_PROMPT_EN
        user_msg = f"Current_Step: {current_step}\nwait_count: {state['wait_count']}\nUser_Input: {text}"
        response = call_claude(prompt, user_msg)
        send_message(phone, response)
        new_step = current_step + 1
        if new_step > 5:
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=new_step, wait_count=0)
        return

    # Routing
    if text in ["א", "a", "A", "1"]:
        set_state(phone, tool="breathing", step=0)
        msg = "בוא נתחיל בנשימות. אני כאן איתך. מוכן?" if lang == "he" else "Let us begin with breathing. I am here with you. Ready?"
        send_message(phone, msg)
        return

    if text in ["ב", "b", "B", "2"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        msg = "בוא נתחיל בתרגיל קרקוע. מוכן?" if lang == "he" else "Let us begin with the grounding exercise. Ready?"
        send_message(phone, msg)
        return

    if text in ["ג", "c", "C", "3"]:
        set_state(phone, tool="none", step=0)
        msg = "תודה שהיית איתנו. אני כאן תמיד כשתצטרך." if lang == "he" else "Thank you. I am always here when you need me."
        send_message(phone, msg)
        return

    prompt = ORCHESTRATOR_PROMPT_HE if lang == "he" else ORCHESTRATOR_PROMPT_EN
    response = call_claude(prompt, text)
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
        print(f"[webhook error] {e}", flush=True)
    return jsonify({"status": "ok"}), 200

@app.route("/", methods=["GET"])
def health():
    return "SafeHarbor Bot is running", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
