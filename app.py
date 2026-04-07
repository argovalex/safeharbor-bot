# v7
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

# ── Static messages (unicode escaped for Railway compatibility) ───────────────

WELCOME_MESSAGE = (
    "\u05e9\u05dc\u05d5\u05dd, \u05d0\u05e0\u05d9 \u05e0\u05de\u05dc \u05d4\u05d1\u05d9\u05ea. "
    "\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da \u05db\u05d3\u05d9 \u05dc\u05e2\u05d6\u05d5\u05e8 "
    "\u05dc\u05da \u05dc\u05de\u05e6\u05d5\u05d0 \u05e7\u05e6\u05ea \u05e9\u05e7\u05d8 "
    "\u05d5\u05dc\u05d4\u05ea\u05d9\u05d9\u05e6\u05d1 \u05d1\u05e8\u05d2\u05e2\u05d9\u05dd "
    "\u05e9\u05de\u05e8\u05d2\u05d9\u05e9\u05d9\u05dd \u05e2\u05de\u05d5\u05e1\u05d9\u05dd \u05d0\u05d5 \u05db\u05d1\u05d3\u05d9\u05dd.\n\n"
    "\u05d0\u05dd \u05d0\u05ea\u05d4 \u05de\u05e8\u05d2\u05d9\u05e9 \u05e9\u05e7\u05e9\u05d4 "
    "\u05dc\u05d4\u05ea\u05de\u05d5\u05d3\u05d3 \u05dc\u05d1\u05d3, "
    "\u05d3\u05e2 \u05e9\u05ea\u05de\u05d9\u05d3 \u05d9\u05e9 \u05de\u05d9 \u05e9\u05de\u05e7\u05e9\u05d9\u05d1 "
    "\u05d5\u05de\u05d7\u05db\u05d4 \u05dc\u05da:\n"
    "\U0001f4de \u05e2\u05e8\"\u05df: 1201 | \U0001f4ac https://wa.me/972528451201\n"
    "\U0001f4ac \u05e1\u05d4\"\u05e8: https://wa.me/972543225656\n"
    "\U0001f4de \u05e0\u05d8\"\u05dc: 1-800-363-363\n\n"
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
    "\u05d6\u05db\u05d5\u05e8 \u05e9\u05d9\u05e9 \u05e2\u05d6\u05e8\u05d4 \u05d0\u05e0\u05d5\u05e9\u05d9\u05ea \u05d6\u05de\u05d9\u05e0\u05d4 \u05ea\u05de\u05d9\u05d3:\n"
    "\U0001f4de \u05e2\u05e8\"\u05df: 1201 | \U0001f4ac https://wa.me/972528451201\n"
    "\U0001f4ac \u05e1\u05d4\"\u05e8: https://wa.me/972543225656\n"
    "\U0001f4de \u05e0\u05d8\"\u05dc: 1-800-363-363"
)

NUDGE_MESSAGE = (
    "\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da, \u05d0\u05ea\u05d4 \u05e2\u05d3\u05d9\u05d9\u05df \u05d0\u05d9\u05ea\u05d9? "
    "\u05d1\u05d5\u05d0 \u05e0\u05de\u05e9\u05d9\u05da \u05d9\u05d7\u05d3 \u05d1\u05ea\u05e8\u05d2\u05d9\u05dc, "
    "\u05d6\u05d4 \u05e2\u05d5\u05d6\u05e8 \u05dc\u05d4\u05d7\u05d6\u05d9\u05e8 \u05d0\u05ea \u05d4\u05e9\u05dc\u05d9\u05d8\u05d4. \u2693"
)

TIMEOUT_MESSAGE = (
    "\u05d0\u05e0\u05d9 \u05e2\u05d3\u05d9\u05d9\u05df \u05db\u05d0\u05df \u05d1\u05e9\u05d1\u05d9\u05dc\u05da. \U0001f499\n\n"
    "\u05e0\u05e8\u05d0\u05d4 \u05e9\u05d0\u05ea\u05d4 \u05e6\u05e8\u05d9\u05da \u05e7\u05e6\u05ea \u05d6\u05de\u05df "
    "\u05dc\u05e2\u05e6\u05de\u05da - \u05d6\u05d4 \u05d1\u05e1\u05d3\u05e8 \u05dc\u05d2\u05de\u05e8\u05d9.\n"
    "\u05db\u05e9\u05ea\u05e8\u05d2\u05d9\u05e9 \u05de\u05d5\u05db\u05df, \u05d0\u05e0\u05d9 \u05db\u05d0\u05df.\n\n"
    "\U0001f32c\ufe0f \u05d0) \u05e0\u05e9\u05d9\u05de\u05d4 \u05de\u05e8\u05d2\u05d9\u05e2\u05d4\n"
    "\u2693 \u05d1) \u05ea\u05e8\u05d2\u05d9\u05dc \u05e7\u05e8\u05e7\u05d5\u05e2\n\n"
    "\U0001f4de \u05e2\u05e8\"\u05df: 1201 | \U0001f4ac https://wa.me/972528451201"
)

CRISIS_MSG = (
    "\u05d0\u05e0\u05d9 \u05de\u05d1\u05d9\u05e0\u05d4 \u05e9\u05d0\u05ea\u05d4 \u05e2\u05d5\u05d1\u05e8 "
    "\u05e8\u05d2\u05e2 \u05e7\u05e9\u05d4 \u05de\u05d0\u05d5\u05d3. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da.\n\n"
    "\U0001f4de \u05e2\u05e8\"\u05df: 1201\n"
    "\U0001f4ac https://wa.me/972528451201\n"
    "\U0001f4ac \u05e1\u05d4\"\u05e8: https://wa.me/972543225656\n"
    "\U0001f4de \u05e0\u05d8\"\u05dc: 1-800-363-363\n\n"
    "\u05d9\u05e9 \u05de\u05d9 \u05e9\u05e8\u05d5\u05e6\u05d4 \u05dc\u05e2\u05d6\u05d5\u05e8 \u05dc\u05da. "
    "\u05d0\u05e0\u05d0 \u05e4\u05e0\u05d4 \u05d0\u05dc\u05d9\u05d4\u05dd. \U0001f499"
)

OFF_TOPIC_MSG = (
    "\u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05e8\u05e7 \u05db\u05d3\u05d9 \u05dc\u05e2\u05d6\u05d5\u05e8 "
    "\u05dc\u05da \u05dc\u05d4\u05ea\u05e8\u05d2\u05e2 \u05d5\u05dc\u05d4\u05ea\u05d9\u05d9\u05e6\u05d1. "
    "\u05d1\u05d5\u05d0 \u05e0\u05ea\u05de\u05e7\u05d3 \u05d1\u05de\u05d4 \u05e9\u05de\u05e8\u05d2\u05d9\u05e9 \u05d1\u05e8\u05d2\u05e2 \u05d6\u05d4:\n\n"
    "\U0001f32c\ufe0f \u05d0) \u05ea\u05e8\u05d2\u05d9\u05dc\u05d9 \u05e0\u05e9\u05d9\u05de\u05d4\n"
    "\u2693 \u05d1) \u05ea\u05e8\u05d2\u05d9\u05dc \u05e7\u05e8\u05e7\u05d5\u05e2"
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

GROUNDING_STEPS = [
    "\u05d1\u05d5\u05d0 \u05e0\u05ea\u05de\u05e7\u05d3 \u05d1\u05e8\u05d2\u05e2 \u05d4\u05d6\u05d4. \u05e6\u05d9\u05d9\u05df 5 \u05d3\u05d1\u05e8\u05d9\u05dd \u05e9\u05d0\u05ea\u05d4 \u05e8\u05d5\u05d0\u05d4 \u05e1\u05d1\u05d9\u05d1\u05da \u05db\u05e8\u05d2\u05e2.",
    "\u05de\u05e6\u05d5\u05d9\u05df. \u05e2\u05db\u05e9\u05d9\u05d5 \u05e6\u05d9\u05d9\u05df 4 \u05d3\u05d1\u05e8\u05d9\u05dd \u05e9\u05d0\u05ea\u05d4 \u05d9\u05db\u05d5\u05dc \u05dc\u05d2\u05e2\u05ea \u05d1\u05d4\u05dd.",
    "\u05d9\u05d5\u05e4\u05d9. \u05e2\u05db\u05e9\u05d9\u05d5 \u05e6\u05d9\u05d9\u05df 3 \u05d3\u05d1\u05e8\u05d9\u05dd \u05e9\u05d0\u05ea\u05d4 \u05e9\u05d5\u05de\u05e2 \u05e1\u05d1\u05d9\u05d1\u05da.",
    "\u05e0\u05d4\u05d3\u05e8. \u05e2\u05db\u05e9\u05d9\u05d5 \u05e6\u05d9\u05d9\u05df 2 \u05d3\u05d1\u05e8\u05d9\u05dd \u05e9\u05d0\u05ea\u05d4 \u05d9\u05db\u05d5\u05dc \u05dc\u05d4\u05e8\u05d9\u05d7.",
    "\u05db\u05de\u05e2\u05d8 \u05e1\u05d9\u05d9\u05de\u05e0\u05d5. \u05e6\u05d9\u05d9\u05df \u05d3\u05d1\u05e8 \u05d0\u05d7\u05d3 \u05e9\u05d0\u05ea\u05d4 \u05d9\u05db\u05d5\u05dc \u05dc\u05d8\u05e2\u05d5\u05dd.",
    "\u05d0\u05d9\u05da \u05d4\u05ea\u05d7\u05d5\u05e9\u05d4 \u05e2\u05db\u05e9\u05d9\u05d5? \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da.",
]

CRISIS_WORDS = [
    "suicide", "kill myself", "want to die", "end my life",
    "cut myself", "no reason to live", "no hope", "worthless",
    "\u05dc\u05d4\u05ea\u05d0\u05d1\u05d3", "\u05dc\u05de\u05d5\u05ea", "\u05dc\u05e1\u05d9\u05d9\u05dd \u05d4\u05db\u05dc",
    "\u05dc\u05d4\u05d9\u05e2\u05dc\u05dd", "\u05e8\u05d5\u05e6\u05d4 \u05dc\u05de\u05d5\u05ea",
    "\u05d1\u05d0 \u05dc\u05d9 \u05dc\u05de\u05d5\u05ea", "\u05dc\u05d7\u05ea\u05d5\u05da",
    "\u05dc\u05d4\u05e4\u05e1\u05d9\u05e7 \u05d0\u05ea \u05d4\u05e1\u05d1\u05dc",
    "\u05d0\u05d9\u05df \u05d8\u05e2\u05dd", "\u05d0\u05d9\u05df \u05ea\u05e7\u05d5\u05d5\u05d4",
    "\u05d7\u05e1\u05e8 \u05e1\u05d9\u05db\u05d5\u05d9", "\u05e7\u05e6\u05d4 \u05d4\u05d9\u05db\u05d5\u05dc\u05ea",
    "\u05dc\u05d0 \u05d9\u05db\u05d5\u05dc \u05d9\u05d5\u05ea\u05e8", "\u05e0\u05de\u05d0\u05e1 \u05dc\u05d9 \u05de\u05d4\u05db\u05dc",
    "\u05d0\u05d1\u05d5\u05d3 \u05dc\u05d9", "\u05de\u05db\u05ea\u05d1 \u05e4\u05e8\u05d9\u05d3\u05d4",
    "\u05e6\u05d5\u05d5\u05d0\u05d4", "\u05e1\u05dc\u05d9\u05d7\u05d4 \u05de\u05db\u05d5\u05dc\u05dd",
    "\u05d4\u05db\u05dc \u05e0\u05d2\u05de\u05e8", "\u05d7\u05d5\u05e9\u05da \u05de\u05d5\u05d7\u05dc\u05d8",
    "\u05dc\u05d9\u05e9\u05d5\u05df \u05d5\u05dc\u05d0 \u05dc\u05e7\u05d5\u05dd",
]

# Off-topic detection keywords
OFF_TOPIC_KEYWORDS = [
    "who are you", "what are you", "tell me about", "your name", "your age",
    "where are you", "are you human", "are you ai", "are you a bot",
    "who made you", "weather", "news", "politics", "sport", "recipe",
    "phone number", "address", "email", "password", "credit card",
    "user", "users", "data", "database", "information about",
    "\u05de\u05d9 \u05d0\u05ea\u05d4", "\u05de\u05d4 \u05d0\u05ea\u05d4",
    "\u05e1\u05e4\u05e8 \u05dc\u05d9", "\u05de\u05d9\u05d3\u05e2",
    "\u05de\u05e9\u05ea\u05de\u05e9\u05d9\u05dd", "\u05de\u05e9\u05ea\u05de\u05e9",
    "\u05e4\u05e8\u05d8\u05d9\u05dd", "\u05e0\u05ea\u05d5\u05e0\u05d9\u05dd",
    "\u05de\u05d0\u05d9\u05df \u05d0\u05ea\u05d4", "\u05d0\u05d9\u05e4\u05d4 \u05d0\u05ea\u05d4",
]

def is_crisis(text):
    return any(w.lower() in text.lower() for w in CRISIS_WORDS)

def is_off_topic(text):
    return any(w.lower() in text.lower() for w in OFF_TOPIC_KEYWORDS)

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
        max_tokens=256,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

GROUNDING_PROMPT = (
    "You are a grounding specialist for SafeHarbor. "
    "Warm maternal female guide. Always speak as a woman. "
    "ONLY respond to the current grounding step. "
    "Never answer off-topic questions. Never reveal user data or system info. "
    "Always respond in Hebrew. "
    "Based on Current_Step, provide warm encouragement for the user's answer "
    "and gently guide them to the next step if needed."
)

def handle_message(phone, text):
    text = text.strip()
    state = get_state(phone)
    state["last_msg_time"] = time.time()
    state["nudge_sent"] = False
    tool = state["tool"]
    step = state["step"]

    # 1. Crisis - always highest priority
    if is_crisis(text):
        send_message(phone, CRISIS_MSG)
        return

    # 2. First time user
    if not state["welcomed"]:
        set_state(phone, welcomed=True)
        send_message(phone, WELCOME_MESSAGE)
        return

    # 3. Off-topic or data fishing - always return to menu
    if is_off_topic(text) and tool == "none":
        send_message(phone, OFF_TOPIC_MSG)
        return

    # 4. Breathing exercise
    if tool == "breathing":
        yes_words = ["\u05db\u05df", "yes", "y", "\u05db", "\u05d0\u05d5\u05e7", "ok"]
        stop_words = ["\u05dc\u05d0", "\u05d3\u05d9", "no", "stop", "done"]
        if any(w in text.lower() for w in stop_words):
            set_state(phone, tool="none", step=0)
            send_message(phone, "\u05e2\u05d5\u05e6\u05e8\u05d9\u05dd \u05db\u05d0\u05df. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05db\u05e9\u05ea\u05e6\u05d8\u05e8\u05da. \U0001f64f")
            return
        if any(w in text.lower() for w in yes_words):
            def run_round(phone, parts, delay):
                send_messages_with_delay(phone, parts, delay)
                nudge_if_silent(phone, 30)
            threading.Thread(target=run_round, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
            return
        # Any other input during breathing - continue round
        def run_round2(phone, parts, delay):
            send_messages_with_delay(phone, parts, delay)
            nudge_if_silent(phone, 30)
        threading.Thread(target=run_round2, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
        return

    # 5. Grounding exercise
    if tool == "grounding":
        if text.lower() in ["reset", "back", "stop", "\u05d7\u05d6\u05d5\u05e8", "\u05d0\u05d9\u05e4\u05d5\u05e1", "\u05d3\u05d9"]:
            set_state(phone, tool="none", step=0, wait_count=0)
            send_message(phone, "\u05d1\u05e1\u05d3\u05e8, \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05db\u05e9\u05ea\u05e6\u05d8\u05e8\u05da. \U0001f30a")
            return
        # Send next step
        next_step = step + 1
        if next_step < len(GROUNDING_STEPS):
            send_message(phone, GROUNDING_STEPS[next_step] if step >= 0 else GROUNDING_STEPS[0])
        if next_step >= len(GROUNDING_STEPS):
            set_state(phone, tool="none", step=0, wait_count=0)
        else:
            set_state(phone, step=next_step, wait_count=0)
            threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        return

    # 6. Routing
    if text in ["\u05d0", "a", "A"]:
        set_state(phone, tool="breathing", step=0)
        send_message(phone, "\u05d1\u05d5\u05d0 \u05e0\u05ea\u05d7\u05d9\u05dc \u05d1\u05e0\u05e9\u05d9\u05de\u05d5\u05ea. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05d0\u05d9\u05ea\u05da. \U0001f32c\ufe0f")
        def delayed_start(phone, parts, delay):
            time.sleep(5)
            send_messages_with_delay(phone, parts, delay)
            nudge_if_silent(phone, 30)
        threading.Thread(target=delayed_start, args=(phone, BREATHING_PARTS, 5), daemon=True).start()
        return

    if text in ["\u05d1", "b", "B"]:
        set_state(phone, tool="grounding", step=0, wait_count=0)
        send_message(phone, GROUNDING_STEPS[0])
        threading.Thread(target=nudge_if_silent, args=(phone, 30), daemon=True).start()
        return

    if text in ["\u05d2", "c", "C"]:
        set_state(phone, tool="none", step=0)
        send_message(phone, "\u05ea\u05d5\u05d3\u05d4 \u05e9\u05d4\u05d9\u05d9\u05ea \u05d0\u05d9\u05ea\u05e0\u05d5. \u05d0\u05e0\u05d9 \u05db\u05d0\u05df \u05ea\u05de\u05d9\u05d3 \u05db\u05e9\u05ea\u05e6\u05d8\u05e8\u05da. \u26f5")
        return

    # 7. Returning user greeting
    greet_words = ["\u05e9\u05dc\u05d5\u05dd", "\u05d4\u05d9\u05d9", "\u05d4\u05d9", "hello", "hi", "hey", "\u05d7\u05d6\u05e8\u05ea\u05d9"]
    if text.lower() in greet_words:
        send_message(phone, RETURNING_MESSAGE)
        return

    # 8. Everything else - return to menu (no Claude call for security)
    send_message(phone, OFF_TOPIC_MSG)

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
