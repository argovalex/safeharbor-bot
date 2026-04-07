# -*- coding: utf-8 -*-
import os
import sys
import time
import threading
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic

# Force UTF-8 encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

app = Flask(__name__)
client = Anthropic()

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "12345")
WHATSAPP_API_URL = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_ID}/messages"

# user_states now includes language detection
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

# ─── Crisis detection (Hebrew + universal keywords) ──────────────────────────
CRISIS_WORDS = [
    # Hebrew
    "להתאבד", "למות", "לסיים הכל", "להיעלם", "רוצה למות",
    "בא לי למות", "לחתוך", "להפסיק את הסבל", "אין טעם",
    "אין תקווה", "חסר סיכוי", "קצה היכולת", "לא יכול יותר",
    "נמאס לי מהכל", "אבוד לי", "מכתב פרידה", "צוואה",
    "סליחה מכולם", "תמסרו להם", "תשמרו על", "הכל נגמר",
    "קבר", "מוות", "עולם הבא", "חושך מוחלט", "לישון ולא לקום",
    # English
    "kill myself", "end my life", "want to die", "suicide",
    "cut myself", "no reason to live", "can't go on",
    "goodbye forever", "no hope", "worthless",
    # Arabic
    "انتحار", "اريد ان اموت", "لا فائدة", "اقتل نفسي",
    # Russian
    "хочу умереть", "покончить с собой", "нет смысла",
    # Spanish
    "quiero morir", "suicidarme", "no hay esperanza",
    # French
    "veux mourir", "me suicider", "plus envie de vivre",
]

def is_crisis(text):
    text_lower = text.lower()
    return any(word.lower() in text_lower for word in CRISIS_WORDS)

def get_crisis_message(lang):
    messages = {
        "he": (
            "אני מבינה שאתה עובר רגע קשה מאוד. אני כאן כדי לתמוך, "
            "אבל חשוב לי לוודא שאתה מקבל את העזרה המקצועית שאתה זקוק לה.\n\n"
            "📞 ער\"ן: 1201 | 💬 וואטסאפ: https://wa.me/972528451201\n"
            "💬 סה\"ר: https://wa.me/972543225656\n"
            "📞 נט\"ל: 1-800-363-363\n\n"
            "יש מי שרוצה לעזור לך. אנא פנה אליהם. 💙"
        ),
        "en": (
            "I understand you're going through a very difficult moment. I'm here to support you, "
            "but it's important that you get the professional help you need.\n\n"
            "📞 Crisis line: 988 (US) | 116 123 (UK)\n"
            "💬 Crisis Text Line: Text HOME to 741741\n"
            "🌐 https://findahelpline.com\n\n"
            "There are people who want to help you. Please reach out to them. 💙"
        ),
        "ar": (
            "أنا أفهم أنك تمر بلحظة صعبة جداً. أنا هنا لدعمك، "
            "لكن من المهم أن تحصل على المساعدة المهنية التي تحتاجها.\n\n"
            "📞 خط مساعدة: 1201\n"
            "🌐 https://findahelpline.com\n\n"
            "هناك من يريد مساعدتك. يرجى التواصل معهم. 💙"
        ),
        "ru": (
            "Я понимаю, что ты переживаешь очень трудный момент. Я здесь, чтобы поддержать тебя, "
            "но важно, чтобы ты получил профессиональную помощь.\n\n"
            "📞 Телефон доверия: 8-800-2000-122 (Россия)\n"
            "🌐 https://findahelpline.com\n\n"
            "Есть люди, которые хотят помочь тебе. Пожалуйста, обратись к ним. 💙"
        ),
        "es": (
            "Entiendo que estás pasando un momento muy difícil. Estoy aquí para apoyarte, "
            "pero es importante que recibas la ayuda profesional que necesitas.\n\n"
            "📞 Teléfono de la Esperanza: 717 003 717\n"
            "🌐 https://findahelpline.com\n\n"
            "Hay personas que quieren ayudarte. Por favor, comunícate con ellas. 💙"
        ),
        "fr": (
            "Je comprends que tu traverses un moment très difficile. Je suis là pour te soutenir, "
            "mais il est important que tu reçoives l'aide professionnelle dont tu as besoin.\n\n"
            "📞 Numéro national de prévention du suicide: 3114\n"
            "🌐 https://findahelpline.com\n\n"
            "Il y a des personnes qui veulent t'aider. S'il te plaît, contacte-les. 💙"
        ),
    }
    return messages.get(lang, messages["en"])

# ─── Language detection via Claude ───────────────────────────────────────────
def detect_language(text):
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": f"Detect the language of this text and reply with ONLY the 2-letter ISO code (he, en, ar, ru, es, fr, de, it, pt, or 'other'). Text: {text}"
            }]
        )
        lang = response.content[0].text.strip().lower()[:2]
        supported = ["he", "en", "ar", "ru", "es", "fr", "de", "it", "pt"]
        return lang if lang in supported else "en"
    except:
        return "en"

# ─── Send WhatsApp message ────────────────────────────────────────────────────
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

# ─── Claude API call ──────────────────────────────────────────────────────────
def call_claude(system_prompt, user_message):
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

# ─── System prompts (language-aware) ─────────────────────────────────────────
def get_orchestrator_prompt(lang):
    base = """You are SafeHarbor – a calm, gentle female guide for emotional support.
Identity: Always speak as a woman ("I'm here with you", "I understand").
Speak to the user warmly. Never perform exercises yourself.
CRITICAL: Always respond in the SAME language the user wrote in.

Standard scenario:
Greet warmly and offer:
🌬️ A) Breathing exercises
⚓ B) Grounding exercise
Ask them to choose A or B.

If they choose A or breathing: confirm and ask if ready.
If they choose B or grounding: confirm and ask if ready.
Use emojis sparingly (🌬️, ⚓, ⛵)."""
    return base

def get_grounding_prompt(lang):
    return """You are the grounding specialist of SafeHarbor. Female guide, gentle and supportive.
Always speak as a woman. Never mention breathing. Never show main menu during steps.
CRITICAL: Always respond in the SAME language as the User_Input.

Based on Current_Step:
0: Ask user to name 5 things they can see around them.
1: Ask for 4 things they can touch right now.
2: Ask for 3 things they can hear around them.
3: Ask for 2 things they can smell.
4: Ask for 1 thing they can taste (or a calming taste).
5: Ask how they feel now, say you're here with them.

If wait_count 1-2: Gently encourage, ask if they found one thing.
If wait_count >= 3: Say you're here when they're ready, suggest typing 'continue' or 'reset'.
If user says reset/stop/back: return gently to main menu."""

def get_breathing_prompt(lang):
    if lang == "he":
        return """את מנחה נשית לתרגילי נשימה של נמל הבית.
החזירי רק את הטקסטים המוגדרים, עם פסיק-נקודה (;) כמפריד.

התחלה/המשך:
אני כאן איתך, נתחיל יחד עכשיו. 🌬️;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;🌬️⬅️ שאיפה איטית... 1-2-3-4-5;✋ עצור... 1-2-3-4-5;🍃➡️ נשיפה איטית... 1-2-3-4-5;⚓ מנוחה... 1-2-3-4-5;סיימנו 3 סבבים. איך התחושה? נמשיך לסבב נוסף? (כן/לא)

עצירה:
עוצרים כאן 🙏;אם תרצה לחזור – אני כאן.;מה מרגיש לך נכון יותר ברגע הזה?;🌬️ א) נשימה מרגיעה;⚓ ב) תרגיל קרקוע;👋 ג) סיום;זכור שיש עזרה אנושית זמינה עבורך תמיד:;📞 ער"ן: 1201;💬 https://wa.me/972528451201;📞 נט"ל: 1-800-363-363"""
    else:
        return """You are a female breathing exercise guide for SafeHarbor.
Return ONLY the defined sequences below, using semicolon (;) as separator.
ALWAYS respond in the same language as the user's input.

Start/Continue (yes, start, more, continue):
I'm here with you, let's begin together. 🌬️;🌬️⬅️ Breathe in slowly... 1-2-3-4-5;✋ Hold... 1-2-3-4-5;🍃➡️ Breathe out slowly... 1-2-3-4-5;⚓ Rest... 1-2-3-4-5;🌬️⬅️ Breathe in slowly... 1-2-3-4-5;✋ Hold... 1-2-3-4-5;🍃➡️ Breathe out slowly... 1-2-3-4-5;⚓ Rest... 1-2-3-4-5;🌬️⬅️ Breathe in slowly... 1-2-3-4-5;✋ Hold... 1-2-3-4-5;🍃➡️ Breathe out slowly... 1-2-3-4-5;⚓ Rest... 1-2-3-4-5;We completed 3 rounds. How do you feel? Continue? (yes/no)

Stop (no, stop, enough, done):
Stopping here 🙏;I'm here if you want to return.;What feels right for you now?;🌬️ A) Breathing exercise;⚓ B) Grounding exercise;👋 C) End session;Remember, human support is always available:;🌐 https://findahelpline.com"""

# ─── Message handler ──────────────────────────────────────────────────────────
def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)

    # Detect language on first message or if unknown
    if state["lang"] is None:
        lang = detect_language(text)
        set_state(phone, lang=lang)
    else:
        lang = state["lang"]

    # Crisis detection - highest priority
    if is_crisis(text):
        send_message(phone, get_crisis_message(lang))
        return

    current_tool = state["tool"]
    current_step = state["step"]

    # Breathing
    if current_tool == "breathing":
        response = call_claude(get_breathing_prompt(lang), f"User_Input: {text}")
        parts = [p for p in response.split(";") if p.strip()]
        stop_words = ["לא", "די", "מספיק", "no", "stop", "enough", "done", "لا", "нет", "no"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)
        threading.Thread(target=send_messages_with_delay, args=(phone, parts, 5), daemon=True).start()
        return

    # Grounding
    if current_tool == "grounding":
        reset_words = ["חזור", "די", "איפוס", "back", "stop", "reset", "cancel"]
        if any(w in text.lower() for w in reset_words):
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "I'm here when you need me. 🌊" if lang != "he" else "בסדר, חוזרים. אני כאן כשתצטרך. 🌊")
            return
        user_msg = f"Current_Step: {current_step}\nwait_count: {state['wait_count']}\nUser_Input: {text}"
        response = call_claude(get_grounding_prompt(lang), user_msg)
        send_message(phone, response)
        new_step = current_step + 1
        if new_step > 5:
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=new_step, wait_count=0)
        return

    # Routing
    breathing_triggers = ["א", "נשימה", "a", "breathing", "breath", "А", "а"]
    grounding_triggers = ["ב", "קרקוע", "b", "grounding", "ground", "Б", "б"]
    end_triggers = ["ג", "סיום", "c", "end", "bye", "exit"]

    if any(t in text for t in breathing_triggers):
        set_state(phone, tool="breathing", step=0)
        msg = "בוא נתחיל בנשימות. אני כאן איתך. מוכן?" if lang == "he" else "Let's begin with breathing. I'm here with you. Ready?"
        send_message(phone, msg)
        return

    if any(t in text for t in grounding_triggers):
        set_state(phone, tool="grounding", step=0, wait_count=0)
        msg = "בוא נתחיל בתרגיל קרקוע. מוכן?" if lang == "he" else "Let's begin with the grounding exercise. Ready?"
        send_message(phone, msg)
        return

    if any(t in text for t in end_triggers):
        set_state(phone, tool="none", step=0)
        msg = "תודה שהיית איתנו. אני כאן תמיד כשתצטרך. ⛵" if lang == "he" else "Thank you for being with us. I'm always here when you need me. ⛵"
        send_message(phone, msg)
        return

    # Orchestrator
    response = call_claude(get_orchestrator_prompt(lang), text)
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
