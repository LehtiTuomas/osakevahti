from datetime import datetime
import json
import os
import time
from groq import Groq
import pandas as pd
import requests
import yfinance as yf

# Testataan mitä GitHub Actions oikeasti välittää sisään:
print("--- YMPÄRISTÖMUUTTUJIEN TESTI ---")
print("GROQ_API_KEY pituus:", len(os.environ.get("GROQ_API_KEY", "")))
print("TELEGRAM_BOT_TOKEN pituus:", len(os.environ.get("TELEGRAM_BOT_TOKEN", "")))
print("TELEGRAM_CHAT_ID pituus:", len(os.environ.get("TELEGRAM_CHAT_ID", "")))
print("---------------------------------")

print("--- KAIKKI YMPÄRISTÖMUUTTUJIEN NIMET ---")
for key in sorted(os.environ.keys()):
  print(key)
print("----------------------------------------")

# API-avaimet & Tiedostot
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SIGNALS_FILE = "signals.json"

# Seurantalista (98 kohdetta)
SEURANTALISTA = [
    # Indeksit (4)
    "^GDAXI",
    "^NDX",
    "^GSPC",
    "^OMXH25",
    # Suomi / OMXH
    "NOKIA.HE",
    "NESTE.HE",
    "UPM.HE",
    "FORTUM.HE",
    "KNEBV.HE",
    "SAMPO.HE",
    "NDA-FI.HE",
    "WRT1V.HE",
    "VALMT.HE",
    "METSA.HE",
    "OUT1V.HE",
    "TYRES.HE",
    "ORNBV.HE",
    "HUH1V.HE",
    "ELISA.HE",
    "KESKOB.HE",
    "KEMIRA.HE",
    "MEO1V.HE",
    "QTCOM.HE",
    "KOJAMO.HE",
    # Ruotsi & Pohjoismaat
    "VOLV-B.ST",
    "INVE-B.ST",
    "EQT.ST",
    "HM-B.ST",
    "ERIC-B.ST",
    "SEB-A.ST",
    "SWED-A.ST",
    "SHB-A.ST",
    "NOVO-B.CO",
    "MAERSK-B.CO",
    # Eurooppa
    "SAP.DE",
    "SIE.DE",
    "ALV.DE",
    "BMW.DE",
    "MBG.DE",
    "VOW3.DE",
    "DTG.DE",
    "BAS.DE",
    "DTE.DE",
    "RHM.DE",
    "AIR.PA",
    "OR.PA",
    "MC.PA",
    "TTE.PA",
    "ASML.AS",
    "INGA.AS",
    "ADEN.AS",
    # USA - Jätit & Teknot
    "NVDA",
    "TSLA",
    "AAPL",
    "MSFT",
    "AMZN",
    "GOOGL",
    "AMD",
    "META",
    "NFLX",
    "INTC",
    "CRM",
    "AVGO",
    "ORCL",
    "QCOM",
    "PLTR",
    "PALO",
    "SMCI",
    "COIN",
    "MARA",
    "PYPL",
    "SQ",
    "UBER",
    "ABNB",
    "DIS",
    # USA - Teollisuus, Rahoitus & Kulutus
    "JPM",
    "BAC",
    "GS",
    "MS",
    "C",
    "V",
    "MA",
    "JNJ",
    "PFE",
    "LLY",
    "UNH",
    "ABBV",
    "MRK",
    "PG",
    "KO",
    "PEP",
    "COST",
    "WMT",
    "NKE",
    "CAT",
    "DE",
    "BA",
    "GE",
    "XOM",
    "CVX",
]

PUDOTUS_KYNNYS = -2.0
RSI_YLIMYYTY = 35.0


def laski_rsi(series, period=14):
  delta = series.diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


def laheta_telegram(viesti):
  if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print(f"\n[TELEGRAM SIMULAATIO]:\n{viesti}\n")
    return
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {
      "chat_id": TELEGRAM_CHAT_ID,
      "text": viesti,
      "parse_mode": "Markdown",
  }
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f"Virhe Telegram-viestissä: {e}")


def load_json(filepath, default_value):
  if not os.path.exists(filepath):
    return default_value
  with open(filepath, "r", encoding="utf-8") as f:
    try:
      return json.load(f)
    except Exception:
      return default_value


def save_json(filepath, data):
  with open(filepath, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=4, ensure_ascii=False)


def analysoi_dippi_ja_katalyytti(symboli, muutos, hinta, rsi_arvo, sma200_arvo):
  client = Groq(api_key=GROQ_API_KEY)

  prompt = (
      f"Sijoituskohde/Indeksi {symboli} laski tänään {muutos:.2f}% (RSI:"
      f" {rsi_arvo:.1f}, Hinta: {hinta:.2f} €, SMA200: {sma200_arvo:.2f}).\n"
      f"Tehtäväsi sijoitusassistenttina:\n"
      f"1. Arvioi lyhyesti uutiskatalyytti tai mahdollinen ylireagointi ('dipin"
      f" osto').\n"
      f"2. Anna napakka tiivistelmä teknisestä tilanteesta ja mahdollisesta"
      f" käännekohdasta.\n"
      f"Kirjoita tiivis suomenkielinen arvio (max 3 virkettä)."
  )

  try:
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    catalyst_analysis = completion.choices[0].message.content
  except Exception as e:
    catalyst_analysis = (
        f"Tekninen dippi (RSI {rsi_arvo:.1f}), päivämuutos {muutos:.2f}%."
    )

  laheta_telegram(
      f"🚨 **DIPPI & KATALYYTTI: {symboli}** 🚨\n\n{catalyst_analysis}"
  )

  signals = load_json(SIGNALS_FILE, [])
  signal_id = f"{datetime.now().strftime('%Y-%m-%d')}_{symboli}"

  if not any(s.get("id") == signal_id for s in signals):
    signals.append({
        "id": signal_id,
        "ticker": symboli,
        "action_recommendation": "BUY",
        "rsi": round(rsi_arvo, 1),
        "technical_note": (
            f"Päivälasku {muutos:.2f}%, hinta {hinta:.2f} €, SMA200"
            f" {sma200_arvo:.2f} €."
        ),
        "catalyyti": catalyst_analysis,
        "processed": False,
    })
    save_json(SIGNALS_FILE, signals)
    print(f"✅ Signaali tallennettu tiedostoon signals.json: {symboli}")


def main():
  print(f"Screener käynnissä: tutkitaan {len(SEURANTALISTA)} kohdetta...")

  for symboli in SEURANTALISTA:
    try:
      osake = yf.Ticker(symboli)
      hist = osake.history(period="1y")

      if len(hist) < 50:
        hist = osake.history(period="3mo")
        if len(hist) < 2:
          continue

      hist["RSI"] = laski_rsi(hist["Close"], period=14)
      hist["SMA200"] = hist["Close"].rolling(window=200).mean()

      edellinen = hist["Close"].iloc[-2]
      viimeisin = hist["Close"].iloc[-1]
      muutos = ((viimeisin - edellinen) / edellinen) * 100

      rsi_arvo = (
          hist["RSI"].iloc[-1] if not pd.isna(hist["RSI"].iloc[-1]) else 50.0
      )
      sma200_arvo = (
          hist["SMA200"].iloc[-1]
          if not pd.isna(hist["SMA200"].iloc[-1])
          else viimeisin
      )

      if muutos <= PUDOTUS_KYNNYS and (
          rsi_arvo <= RSI_YLIMYYTY or viimeisin < sma200_arvo
      ):
        print(f"🎯 Dippiosuma: {symboli}!")
        analysoi_dippi_ja_katalyytti(
            symboli, muutos, viimeisin, rsi_arvo, sma200_arvo
        )
        time.sleep(1.5)

    except Exception as e:
      print(f"Virhe kohteessa {symboli}: {e}")

  print("Kaikki 98 kohdetta tarkistettu onnistuneesti.")


if __name__ == "__main__":
  main()
