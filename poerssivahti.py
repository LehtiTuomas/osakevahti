import os
from datetime import datetime
import pandas as pd
import requests
import yfinance as yf
from groq import Groq

# 1. HAE YMPÄRISTÖMUUTTUJAT
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Laajennettu seurantalista (voit lisätä tänne helposti lisää tickereitä)
TICKERS = [
    "NOKIA.HE",
    "UPM.HE",
    "WRT1V.HE",
    "ELN1V.HE",
    "KNEBV.HE",
    "SAMPO.HE",
    "FORTUM.HE",
    "STEAV.HE",
    "SIE.DE",
    "PLTR",
    "NVDA",
    "AAPL",
    "TSLA",
    "MSFT",
]

# Tiedosto, johon muistetaan tehdyt hälytykset (estää saman päivän spämmäyksen)
HISTORY_FILE = "lahetetyt_lokit.txt"


def send_telegram_message(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  requests.post(url, json=payload)


def lue_historia():
  if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
      return set(f.read().splitlines())
  return set()


def tallenna_historia(ticker_symbol):
  today_str = datetime.now().strftime("%Y-%m-%d")
  record = f"{ticker_symbol}_{today_str}"
  with open(HISTORY_FILE, "a") as f:
    f.write(record + "\n")


def calculate_rsi(series, period=14):
  delta = series.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


def analyze_and_filter_stock(ticker_symbol, lahetetyt_tanaan):
  today_str = datetime.now().strftime("%Y-%m-%d")
  if f"{ticker_symbol}_{today_str}" in lahetetyt_tanaan:
    print(
        f"[{ticker_symbol}] On jo saanut hälytyksen tänään. Ohitetaan muistiin"
        " vedoten."
    )
    return

  ticker = yf.Ticker(ticker_symbol)

  # Haetaan päivädata trendeille ja RSI:lle
  df = ticker.history(period="1y")
  if len(df) < 200:
    print(f"[{ticker_symbol}] Liian vähän historiadataa.")
    return

  df["SMA50"] = df["Close"].rolling(window=50).mean()
  df["SMA200"] = df["Close"].rolling(window=200).mean()
  df["RSI"] = calculate_rsi(df["Close"])

  current_price = df["Close"].iloc[-1]
  prev_close = df["Close"].iloc[-2]
  day_change_pct = ((current_price - prev_close) / prev_close) * 100
  current_rsi = df["RSI"].iloc[-1]
  sma50 = df["SMA50"].iloc[-1]
  sma200 = df["SMA200"].iloc[-1]

  # --- 1. TEKNINEN ESISUODATIN ---
  is_dipping = day_change_pct <= -1.5
  is_rsi_low = current_rsi <= 45
  is_in_long_term_uptrend = current_price > (sma200 * 0.95)

  if not ((is_dipping or is_rsi_low) and is_in_long_term_uptrend):
    print(f"[{ticker_symbol}] Ei läpäissyt perusesisuodatusta.")
    return

  # --- 2. PÖRSSIPÄIVÄN / VIIMEISTEN TUNTIEN VIHREÄ KYNTTILÄ -TARKISTUS ---
  # Haetaan tuntidataa (viimeiset päivät), jotta näkyy viimeisten tuntien suunta
  df_hourly = ticker.history(period="5d", interval="1h")
  if not df_hourly.empty and len(df_hourly) >= 4:
    # Tarkistetaan viimeisten 4 tunnin kynttilöiden kehitys (että ostajat ovat heränneet)
    last_4_hours = df_hourly.tail(4)
    # Katsotaan onko viimeisen neljän tunnin jaksossa kokonaissuunta ylöspäin (viimeinen hinta > 4h takainen hinta)
    price_4h_ago = last_4_hours["Close"].iloc[0]
    recent_momentum_positive = current_price >= price_4h_ago

    if not recent_momentum_positive:
      print(
          f"[{ticker_symbol}] Hylätty: Vaikka dipissä ollaan, viimeisen 4h"
          " kynttilät ovat edelleen laskusuunnassa (ei pohjakäännettä)."
      )
      return
  else:
    print(f"[{ticker_symbol}] Tuntidataa ei saatavilla, luotetaan päivädataan.")

  # --- 3. GROQ AI-ANALYYSI ---
  client = Groq(api_key=GROQ_API_KEY)

  prompt = f"""
Olet kokenut swing-kaupankäynnin riskienhallintaan erikoistunut analyytikko.
Arvioi kriittisesti, onko tämä osakeostos potentiaalinen käännekohta vai riskipitoinen "putoava puukko".

OSAKE: {ticker_symbol}
- Nykyinen hinta: {current_price:.2f} €/$
- Päivämuutos: {day_change_pct:.2f} %
- RSI (14d): {current_rsi:.1f}
- SMA50: {sma50:.2f} | SMA200: {sma200:.2f}
- Viimeisten tuntien momentum: Osoittaa elpymisen merkkejä

TÄRKEÄ TEHTÄVÄ:
1. Jos kyseessä on huonojen uutisten repimä osake, jolla on vaara valua alemmas, vastaa ehdottomasti EIVAHVISTETA.
2. Jos tämä ON laadukas dippi, joka tarjoaa hyvän rekyylipaikan, vastaa TÄSMÄLLEEN seuraavalla muotoilulla (älä lisää mitään muuta):

🚨 DIPPI, KATALYYTTI & NORDNET-TUOTE: {ticker_symbol} 🚨
- Tunnusluvut: Päivälasku {day_change_pct:.2f} %, RSI {current_rsi:.1f}, Hinta {current_price:.2f} €
- Katalyytti-arvio: [Lyhyt 1-2 lauseen arvio siitä, miksi tämä dippi tarjoaa hyvän paikan]
- Nordnet Markets -tuotehässäkkä: [Suosittele sopivaa Nordnet Mini Long -tuotetta ja sopivaa viputasoa]
- Hakuvinkki Nordnetiin: [Anna täsmällinen hakusana Nordnetiin]
"""

  response = client.chat.completions.create(
      model="openai/gpt-oss-120b",
      messages=[{"role": "user", "content": prompt}],
      temperature=0.1,
  )

  ai_output = response.choices[0].message.content.strip()

  if "EIVAHVISTETA" in ai_output or len(ai_output) < 50:
    print(f"[{ticker_symbol}] AI hylkäsi tapauksen.")
    return

  # Lähetetään viesti ja tallennetaan muistiin, ettei tänään tule toista
  send_telegram_message(ai_output)
  tallenna_historia(ticker_symbol)
  print(f"[{ticker_symbol}] Laadukas hälytys lähetetty Telegramiin!")


def main():
  print("Käynnistetään älykäs pörssivahti...")
  lahetetyt_tanaan = lue_historia()

  for ticker in TICKERS:
    try:
      analyze_and_filter_stock(ticker, lahetetyt_tanaan)
    except Exception as e:
      print(f"Virhe osakkeen {ticker} käsittelyssä: {e}")


if __name__ == "__main__":
  main()
