import os
from groq import Groq
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))


def poll_telegram_messages():
  if not TELEGRAM_BOT_TOKEN:
    return

  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
  try:
    response = requests.get(url, timeout=10).json()
  except Exception:
    return

  if not response.get("ok"):
    return

  for result in response.get("result", []):
    message = result.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if text and not text.startswith("/"):
      prompt = f"""
            Olet asiantunteva sijoitusassistentti. Käyttäjä kysyy sinulta seuraavaa:
            "{text}"
            
            Analysoi tilanne ammattimaisesti, huomioi riskit ja anna selkeä näkemys suomeksi.
            """
      try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
        )
        answer = completion.choices[0].message.content
      except Exception as e:
        answer = f"Virhe tekoälyvastauksessa: {e}"

      send_url = (
          f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
      )
      requests.post(
          send_url,
          json={
              "chat_id": chat_id,
              "text": f"🤖 <b>AI-Assistentin vastaus:</b>\n\n{answer}",
              "parse_mode": "HTML",
          },
          timeout=10,
      )


if __name__ == "__main__":
  poll_telegram_messages()