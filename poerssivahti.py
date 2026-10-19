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
  df["SMA50"] = df["Close"].rolling(window=50).mean()
  df["SMA200"] = df["Close"].rolling(window=200).mean()
  df["RSI"] = calculate_rsi(df["Close"])

  current_price = df["Close"].iloc[-1]
  prev_close = df["Close"].iloc[-2]
  day_change_pct = ((current_price - prev_close) / prev_close) * 100
  current_rsi = df["RSI"].iloc[-1]
  sma50 = df["SMA50"].iloc[-1]
  sma200 = df["SMA200"].iloc[-1]

  # --- TEHNIKEN LAATUESISUODATIN (Tehostettu) ---
  is_dipping = day_change_pct <= -1.5
  is_rsi_low = current_rsi <= 45  # Nostettu hieman herkemmäksi

  # Vaaditaan, että osake on päätrendissään yhä terveellä pohjalla 
  # (hinta vähintään lähellä tai yli SMA200:n, estää pysyvän syöksykierteen)
  is_in_long_term_uptrend = current_price > (sma200 * 0.95)

  if not ((is_dipping or is_rsi_low) and is_in_long_term_uptrend):
    print(f"[{ticker_symbol}] Ei läpäissyt tehostettua teknistä seulaa (trendi tai dippi ei riitä).")
    return

  # --- GROQ AI-ANALYYSI (Käytetään toimivaksi todettua mallia) ---
  client = Groq(api_key=GROQ_API_KEY)

  prompt = f"""
Olet kokenut swing-kaupankäynnin riskienhallintaan erikoistunut analyytikko.
Arvioi kriittisesti, onko tämä osakeostos potentiaalinen vai riskipitoinen "putoava puukko".

OSAKE: {ticker_symbol}
- Nykyinen hinta: {current_price:.2f} €/$
- Päivämuutos: {day_change_pct:.2f} %
- RSI (14d): {current_rsi:.1f}
- SMA50: {sma50:.2f} | SMA200: {sma200:.2f}

TÄRKEÄ TEHTÄVÄ:
1. Jos kyseessä on huonojen uutisten repimä osake, jolla on vaara valua alemmas, vastaa ehdottomasti EIVAHVISTETA.
2. Jos tämä ON laadukas dippi nousutrendissä, vastaa TÄSMÄLLEEN seuraavalla muotoilulla (älä lisää mitään ylimääräistä):

🚨 DIPPI, KATALYYTTI & NORDNET-TUOTE: {ticker_symbol} 🚨
- Tunnusluvut: Päivälasku {day_change_pct:.2f} %, RSI {current_rsi:.1f}, Hinta {current_price:.2f} €
- Katalyytti-arvio: [Lyhyt 1-2 lauseen arvio siitä, miksi tämä dippi tarjoaa hyvän paikan]
- Nordnet Markets -tuotehässäkkä: [Suosittele sopivaa Nordnet Mini Long -tuotetta ja sopivaa viputasoa]
- Hakuvinkki Nordnetiin: [Anna täsmällinen hakusana Nordnetiin]
"""

  response = client.chat.completions.create(
      model="openai/gpt-oss-120b",
      messages=[{"role": "user", "content": prompt}],
      temperature=0.1,  # Pidetään tiukkana ja analyyttisenä
  )

  ai_output = response.choices[0].message.content.strip()

  if "EIVAHVISTETA" in ai_output or len(ai_output) < 50:
    print(f"[{ticker_symbol}] AI hylkäsi tapauksen.")
    return

  send_telegram_message(ai_output)
  print(f"[{ticker_symbol}] Tehostettu hälytys lähetetty Telegramiin!")


def main():
  print("Käynnistetään tehostettu pörssivahti...")
  for ticker in TICKERS:
    try:
      analyze_and_filter_stock(ticker)
    except Exception as e:
      print(f"Virhe osakkeen {ticker} käsittelyssä: {e}")


if __name__ == "__main__":
  main()
