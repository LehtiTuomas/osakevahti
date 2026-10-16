import os
import requests
import yfinance as yf
from groq import Groq

# Haetaan salaisuudet ympäristömuuttujista
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

OSAKE_SYMBOLI = "NOKIA.HE"  # Esim. Nokia (Helsingin pörssi) tai "AAPL" (Apple)
MUUTOS_KYNNYS = -2.0        # Varoitus, jos laskee yli 2%

def laheta_telegram(viesti):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": viesti}
    requests.post(url, json=payload)

def main():
    # Haetaan 5 päivän historia, jotta saadaan varmasti riittävästi rivejä vertailuun
    osake = yf.Ticker(OSAKE_SYMBOLI)
    hist = osake.history(period="5d")
    
    if len(hist) < 2:
        print("Dataa ei saatu riittävästi.")
        return

    # Otetaan aina kaksi viimeisintä saatavilla olevaa kaupankäyntipäivää
    edellinen_paiva = hist['Close'].iloc[-2]
    viimeisin_hinta = hist['Close'].iloc[-1]
    muutos_prosentti = ((viimeisin_hinta - edellinen_paiva) / edellinen_paiva) * 100

    print(f"{OSAKE_SYMBOLI} hinta: {viimeisin_hinta:.2f} € (Muutos: {muutos_prosentti:.2f}%)")

    if muutos_prosentti <= MUUTOS_KYNNYS:
        # Käytetään Groqia (Llama 3 8B -mallia)
        client = Groq(api_key=GROQ_API_KEY)
        
        prompt = (
            f"Osakkeen {OSAKE_SYMBOLI} hinta on muuttunut {muutos_prosentti:.2f}%. "
            f"Nykytaso on {viimeisin_hinta:.2f} €. Kirjoita todella tiivis (max 3 lausetta) "
            f"ja selkeä suomenkielinen varoitusviesti sijoittajalle Telegramiin."
        )
        
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        
        vastaus_teksti = completion.choices[0].message.content
        viesti = f"🚨 **PÖRSSIVAHTI HÄLYTYS** 🚨\n\n{vastaus_teksti}"
        laheta_telegram(viesti)
        print("Hälytys lähetetty Telegramiin!")
    else:
        print("Ei aihetta hälytykseen. (Tarkistus tehty onnistuneesti)")

if __name__ == "__main__":
    main()
