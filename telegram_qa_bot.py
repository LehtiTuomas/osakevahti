import os
from groq import Groq
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)


def poll_telegram_messages():
  if not TELEGRAM_BOT_TOKEN:
    print("❌ Virhe: TELEGRAM_BOT_TOKEN puuttuu ympäristömuuttujista!")
    return

  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
  try:
    print("🔍 Haetaan Telegram-päivityksiä...")
    response = requests.get(url, timeout=10).json()
    print(f"Telegram API vastaus: {response}")
  except Exception as e:
    print(f"❌ Virhe haettaessa Telegram-päivityksiä: {e}")
    return

  if not response.get("ok"):
    print("❌ Telegram API ei palauttanut 'ok'-statusta.")
    return

  results = response.get("result", [])
  if not results:
    print(
        "ℹ️ Ei uusia viestejä Telegramissa. (Muista lähettää viesti botille"
        " ennen työnkulun ajoa!)"
    )
    return

  for result in results:
    message = result.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if text and not text.startswith("/"):
      print(f"💬 Löydetty viesti chatista {chat_id}: '{text}'")

      prompt = (
          f"Olet asiantunteva sijoitusassistentti. Käyttäjä kysyy sinulta"
          f' seuraavaa:\n"{text}"\n\nAnalysoi tilanne ammattimaisesti, huomioi'
          " riskit ja anna selkeä näkemys suomeksi."
      )
      try:
        print("🤖 Pyydetään vastausta Groq AI:lta...")
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
        )
        answer = completion.choices[0].message.content
        print("✅ AI-vastaus generoitu onnistuneesti.")
      except Exception as e:
        answer = f"Virhe tekoälyvastauksessa: {e}"
        print(f"❌ Groq API virhe: {e}")

      send_url = (
          f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
      )
      payload = {
          "chat_id": chat_id,
          "text": f"🤖 <b>AI-Assistentin vastaus:</b>\n\n{answer}",
          "parse_mode": "HTML",
      }
      try:
        res = requests.post(send_url, json=payload, timeout=10)
        print(f"📤 Lähetys Telegramiin vastausstatus: {res.status_code}")
      except Exception as e:
        print(f"❌ Virhe viestin lähetyksessä Telegramiin: {e}")


if __name__ == "__main__":
  poll_telegram_messages()

if __name__ == "__main__":
  poll_telegram_messages()
