import os
import pandas as pd
import requests
import yfinance as yf
from groq import Groq

# 1. HAE YMPÄRISTÖMUUTTUJAT
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Seurattavat osakkeet
TICKERS = ["NOKIA.HE", "UPM.HE", "WRT1V.HE", "SIE.DE", "PLTR", "NVDA", "AAPL"]


def send_telegram_message(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  requests.post(url, json=payload)


def calculate_rsi(series, period=14):
  delta = series.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


def analyze_and_filter_stock(ticker_symbol):
  ticker = yf.Ticker(ticker_symbol)
  df = ticker.history(period="1y")

  if len(df) < 200:
    print(f"[{ticker_symbol}] Liian vähän historiadataa.")
    return

  # Teknisten indikaattorien laskenta
  df["SMA200"] = df["Close"].rolling(window=200).mean()
  df["RSI"] = calculate_rsi(df["Close"])

  current_price = df["Close"].iloc[-1]
  prev_close = df["Close"].iloc[-2]
  day_change_pct = ((current_price - prev_close) / prev_close) * 100
  current_rsi = df["RSI"].iloc[-1]
  sma200 = df["SMA200"].iloc[-1]

  # --- TEKNINEN ESISUODATIN ---
  is_dipping = day_change_pct <= -1.5
  is_rsi_low = current_rsi <= 42
  near_sma200 = abs(current_price - sma200) / sma200 <= 0.03

  if not (is_dipping or is_rsi_low or near_sma200):
    print(f"[{ticker_symbol}] Ei teknistä ostosignaalia, ohitetaan.")
    return

  # --- GROQ AI-ANALYYSI JA LOPULLINEN SUODATUSTESTI ---
  client = Groq(api_key=GROQ_API_KEY)

  prompt = f"""
Olet kokenut swing-kaupankäynnin analyytikko ja Nordnet-sijoittaja.
Analysoi seuraava osake ja päätä, täyttääkö se laadukkaan swing-ostopaikan kriteerit (dippi ilman yhtiön rakenteellista rikkoutumista, rekyylipotentiaali ylöspäin).

OSAKE: {ticker_symbol}
- Nykyinen hinta: {current_price:.2f} €/$
- Päivämuutos: {day_change_pct:.2f} %
- RSI (14d): {current_rsi:.1f}
- 200 päivän keskiarvo (SMA200): {sma200:.2f}

TÄRKEÄ TEHTÄVÄ:
1. Jos kyseessä on vain "putoava puukko" tai huono turha pudotus (tai osakkeessa ei ole aitoa rekyylimahdollisuutta), vastaa EIVAHVISTETA.
2. Jos tämä ON strategiaan sopiva ostopaikka, vastaa TÄSMÄLLEEN seuraavalla muotoilulla (älä lisää mitään ylimääräistä johdantoa):

🚨 DIPPI, KATALYYTTI & NORDNET-TUOTE: {ticker_symbol} 🚨
- Tunnusluvut: Päivälasku {day_change_pct:.2f} %, RSI {current_rsi:.1f}, Hinta {current_price:.2f} €
- Katalyytti-arvio: [Lyhyt 1-2 lauseen arvio siitä, miksi tämä dippi tarjoaa hyvän paikan ja onko kyseessä tekninen korjaus liitteenä markkinaan]
- Nordnet Markets -tuotehässäkkä: [Suosittele sopivaa Nordnet Mini Long -tuotetta ja sopivaa viputasoa osakkeen riskiprofiilille]
- Hakuvinkki Nordnetiin: [Anna täsmällinen hakusana Nordnetiin ja vinkki stop-loss -tasosta nykyisen hinnan alapuolella]
"""

  # Käytetään Groqin tuettua ja laajasti käytössä olevaa Llama 3.3 -mallia
  response = client.chat.completions.create(
      model="llama-3.3-70b-versatile",
      messages=[{"role": "user", "content": prompt}],
      temperature=0.2,
  )

  ai_output = response.choices[0].message.content.strip()

  if "EIVAHVISTETA" in ai_output or len(ai_output) < 50:
    print(f"[{ticker_symbol}] AI hylkäsi ehtojen täyttymisen (turha lasku).")
    return

  send_telegram_message(ai_output)
  print(f"[{ticker_symbol}] Hälytys lähetetty Telegramiin!")


def main():
  print("Käynnistetään pörssivahti ja AI-suodatus...")
  for ticker in TICKERS:
    try:
      analyze_and_filter_stock(ticker)
    except Exception as e:
      print(f"Virhe osakkeen {ticker} käsittelyssä: {e}")


if __name__ == "__main__":
  main()
  main()
