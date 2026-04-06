# SafeHarbor WhatsApp Bot 🌊

בוט וואטסאפ לנמל הבית - ללא Make, ישירות עם Python + Claude.

## פריסה ב-Railway (5 דקות)

### שלב 1 - העלה ל-GitHub
1. צור חשבון ב-[github.com](https://github.com) אם אין לך
2. צור Repository חדש (לחץ + → New repository)
3. העלה את כל הקבצים (app.py, requirements.txt, Procfile)

### שלב 2 - פרוס ב-Railway
1. כנס ל-[railway.app](https://railway.app)
2. התחבר עם חשבון GitHub
3. לחץ **"New Project" → "Deploy from GitHub repo"**
4. בחר את ה-Repository שיצרת

### שלב 3 - הגדר Environment Variables
ב-Railway לחץ על הפרויקט → **Variables** → הוסף:

| שם | ערך |
|---|---|
| `ANTHROPIC_API_KEY` | המפתח שלך מ-console.anthropic.com |
| `WHATSAPP_TOKEN` | ה-Bearer token שלך מ-Meta |
| `WHATSAPP_PHONE_ID` | מספר ה-Phone ID שלך (1060312457161260) |
| `VERIFY_TOKEN` | `12345` (או כל מחרוזת שתבחר) |

### שלב 4 - קבל את ה-URL
אחרי הפריסה Railway יתן לך URL כמו:
`https://safeharbor-production.up.railway.app`

### שלב 5 - חבר ל-Meta
1. כנס ל-Meta Developer Console
2. WhatsApp → Configuration → Webhook
3. **Callback URL:** `https://YOUR-URL.railway.app/webhook`
4. **Verify Token:** `12345`
5. לחץ Verify and Save ✅

## איך זה עובד

```
WhatsApp → Meta → Railway (app.py) → Claude API → WhatsApp
```

- **Orchestrator**: מקבל הודעות ומנתב לכלי הנכון
- **Grounding**: תרגיל 5-4-3-2-1 (6 שלבים)  
- **Breathing**: תרגיל נשימה עם הפסקות של 5 שניות בין הודעות

## קבצים

- `app.py` - כל הלוגיקה של הבוט
- `requirements.txt` - ספריות Python
- `Procfile` - הוראות הפעלה ל-Railway
