import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import yfinance as yf
from groq import Groq

# --- ASETUKSET JA YMPÄRISTÖMUUTTUJAT ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Siivottu seurantalista (98 kohdetta)
TICKERS = [
    # Indeksit (4)
    "^GDAXI", "^NDX", "^GSPC", "^OMXH25",

    # Suomi / OMXH (Nordnet Mini Long / Futuurit)
    "NOKIA.HE", "NESTE.HE", "UPM.HE", "FORTUM.HE", "KNEBV.HE", 
    "SAMPO.HE", "NDA-FI.HE", "WRT1V.HE", "VALMT.HE", "METSA.HE",
    "OUT1V.HE", "TYRES.HE", "ORNBV.HE", "HUH1V.HE", "ELISA.HE",
    "KESKOB.HE", "KEMIRA.HE", "QTCOM.HE",

    # Ruotsi & Pohjoismaat
    "VOLV-B.ST", "INVE-B.ST", "EQT.ST", "HM-B.ST", "ERIC-B.ST",
    "SEB-A.ST", "SWED-A.ST", "SHB-A.ST", "NOVO-B.CO", "MAERSK-B.CO",

    # Eurooppa (Saksa, Ranska, Hollanti)
    "SAP.DE", "SIE.DE", "ALV.DE", "BMW.DE", "MBG.DE", "VOW3.DE",
    "DTG.DE", "BAS.DE", "DTE.DE", "RHM.DE", "AIR.PA", "OR.PA",
    "MC.PA", "TTE.PA", "ASML.AS", "INGA.AS",

    # USA - Jätit & Teknot
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "AMD", "META",
    "NFLX", "INTC", "CRM", "AVGO", "ORCL", "QCOM", "PLTR", "PALO",
    "SMCI", "COIN", "MARA", "PYPL", "UBER", "ABNB", "DIS",

    # USA - Teollisuus, Rahoitus, Terveydenhuolto & Kulutus
    "JPM", "BAC", "GS", "MS", "C", "V", "MA", "JNJ", "PFE", "LLY",
    "UNH", "ABBV", "MRK", "PG", "KO", "PEP", "COST", "WMT", "NKE",
    "CAT", "DE", "BA", "GE", "XOM", "CVX"
]
HISTORY_FILE = "lahetetyt_lokit.txt"
PORTFOLIO_FILE = "salkku_data.json"

def get_finland_time():
    return datetime.now(ZoneInfo("Europe/Helsinki"))

def send_telegram_message(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  requests.post(url, json=payload)


# --- BOTTI 1: VIIKKOMUISTI, UUTISET JA DIPPIVAHTI ---
def lue_historia():
  current_week = get_finland_time().strftime("%Y-W%V")
  if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
      rivit = f.read().splitlines()
    return set(r for r in rivit if current_week in r)
  return set()


def tallenna_historia(ticker_symbol):
  current_week = get_finland_time().strftime("%Y-W%V")
  record = f"{ticker_symbol}_{current_week}"
  with open(HISTORY_FILE, "a") as f:
    f.write(record + "\n")


def hae_uutiset_ja_keskustelut(ticker_obj):
  try:
    news_list = ticker_obj.news
    otsikot = []
    if news_list:
      for item in news_list[:5]:
        title = item.get("content", {}).get("title") or item.get("title", "")
        if title:
          otsikot.append(title)
    return (
        " | ".join(otsikot)
        if otsikot
        else "Ei tuoreita uutisia saatavilla, tekninen liike."
    )
  except Exception:
    return "Uutistietojen haku epäonnistui."


def botti_1_analysoi(ticker_symbol):
  ticker = yf.Ticker(ticker_symbol)
  df = ticker.history(period="1y")

  if len(df) < 200:
    return None, None, None

  df["SMA200"] = df["Close"].rolling(window=200).mean()
  delta = df["Close"].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
  df["RSI"] = 100 - (100 / (1 + (gain / loss)))

  current_price = df["Close"].iloc[-1]
  prev_close = df["Close"].iloc[-2]
  day_change_pct = ((current_price - prev_close) / prev_close) * 100
  current_rsi = df["RSI"].iloc[-1]
  sma200 = df["SMA200"].iloc[-1]

  is_dipping = day_change_pct <= -1.5
  is_rsi_low = current_rsi <= 45
  is_in_long_term_uptrend = current_price > (sma200 * 0.95)

  if not ((is_dipping or is_rsi_low) and is_in_long_term_uptrend):
    return None, None, None

  try:
    df_hourly = ticker.history(period="5d", interval="1h")
    if not df_hourly.empty and len(df_hourly) >= 4:
      last_4_hours = df_hourly.tail(4)
      price_4h_ago = last_4_hours["Close"].iloc[0]
      if current_price < price_4h_ago:
        return None, None, None
  except Exception:
    pass

  uutis_teksti = hae_uutiset_ja_keskustelut(ticker)

  client = Groq(api_key=GROQ_API_KEY)
  prompt = f"""
Olet kokenut osakeanalyytikko. Tarkista, onko kyseessä terveellinen dippi nousutrendissä vai fundamentaalisesti rikki oleva yhtiö.
OSAKE: {ticker_symbol}
- Hinta: {current_price:.2f} | Päivämuutos: {day_change_pct:.2f}% | RSI: {current_rsi:.1f}
- TUOREET UUTISET / MARKKINATUNNELMA: "{uutis_teksti}"

Vastaa TÄSMÄLLEEN muodossa:
PÄÄTÖS: [HYVÄKSYTTY tai HYLÄTTY]
PERUSTELU: [1-2 lauseen analyysi uutisista ja taustasta]
"""

  response = client.chat.completions.create(
      model="openai/gpt-oss-120b",
      messages=[{"role": "user", "content": prompt}],
      temperature=0.2,
  )
  vastaus = response.choices[0].message.content.strip()

  if "HYVÄKSYTTY" in vastaus:
    return current_price, sma200, vastaus

  return None, None, None


# --- BOTTI 2: SALKKU- JA TREIDILOGIIKKA ---
def lataa_salkku():
  if os.path.exists(PORTFOLIO_FILE):
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
      return json.load(f)
  return {"kassa": 3000.00, "alkupääoma": 3000.00, "positiot": []}


def tallenna_salkku(salkku):
  with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
    json.dump(salkku, f, indent=4, ensure_ascii=False)


def botti_2_tee_sijoituspaatos(ticker_symbol, current_price, sma200, botti_1_analyysi):
  salkku = lataa_salkku()

  for pos in salkku["positiot"]:
    if pos["ticker"] == ticker_symbol and pos["tila"] == "AVOIN":
      return

  max_risk_eur = salkku["kassa"] * 0.10

  client = Groq(api_key=GROQ_API_KEY)
  prompt = f"""
Olet itsenäinen, kylmäverinen viputuotteiden (Nordnet Mini Long -minifutuuri) treidausbotti. 
Käteiskassa on {salkku['kassa']:.2f} €. Max panos tälle treidille on {max_risk_eur:.2f} €.

Botti 1 analyysi: "{botti_1_analyysi}"
Kohteen tiedot: {ticker_symbol} | Hinta: {current_price:.2f} | Tukitaso (SMA200): {sma200:.2f}

Tee itsenäinen päätös JSON-muodossa ilman markdown-tageja:
{{
  "sijoita": true,
  "perustelu": "Lyhyt perustelu kaupalle",
  "panos": {round(max_risk_eur, 2)},
  "sisääntulohinta": {current_price},
  "stop_loss": {round(current_price * 0.95, 2)},
  "ensimmainen_tukitaso_tavoite": {round(current_price * 1.06, 2)}
}}
Jos ei sijoitettaisi, aseta "sijoita": false.
"""

  try:
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    res_text = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
    data = json.loads(res_text)

    if data.get("sijoita"):
      uusipositio = {
          "id": len(salkku["positiot"]) + 1,
          "ticker": ticker_symbol,
          "tila": "AVOIN",
          "ostohinta": data["sisääntulohinta"],
          "panos": data["panos"],
          "stop_loss": data["stop_loss"],
          "tavoite": data["ensimmainen_tukitaso_tavoite"],
          "pvm": get_finland_time().strftime("%Y-%m-%d"),
          "osavoitto_kotiutettu": False,
      }
      salkku["kassa"] -= data["panos"]
      salkku["positiot"].append(uusipositio)
      tallenna_salkku(salkku)

      viesti = (
          f"🤖 <b>BOTTI 2: UUSI MINI LONG -TREIDI AVATTU</b> 🤖\n"
          f"- Kohde: {ticker_symbol} (Viputuote / Minifutuuri)\n"
          f"- Panos: {data['panos']:.2f} €\n"
          f"- Perustelu: {data['perustelu']}\n"
          f"- Stop-Loss: {data['stop_loss']:.2f}\n"
          f"- Take Profit (Tavoite): {data['ensimmainen_tukitaso_tavoite']:.2f}"
      )
      send_telegram_message(viesti)
  except Exception as e:
    print(f"Botti 2 päätösvirhe: {e}")


def paivita_avoimet_treidit():
  salkku = lataa_salkku()
  muutos_tehty = False

  for pos in salkku["positiot"]:
    if pos["tila"] != "AVOIN":
      continue
    try:
      ticker = yf.Ticker(pos["ticker"])
      current_price = ticker.history(period="1d")["Close"].iloc[-1]
    except:
      continue

    if not pos["osavoitto_kotiutettu"] and current_price >= pos["tavoite"]:
      pos["osavoitto_kotiutettu"] = True
      myyty_osuus = (pos["panos"] / 2) * (current_price / pos["ostohinta"])
      salkku["kassa"] += myyty_osuus
      pos["stop_loss"] = pos["ostohinta"]
      muutos_tehty = True
      send_telegram_message(
          f"🎯 <b>BOTTI 2: OSAVOITTO KOTIUTETTU ({pos['ticker']} Mini Long)</b>\n"
          f"Kurssi saavutti tason {current_price:.2f}. Myyty 50% potista, riskitön loppupositio!"
      )
    elif current_price <= pos["stop_loss"]:
      pos["tila"] = "SULJETTU"
      jain_arvo = pos["panos"] * (current_price / pos["ostohinta"])
      salkku["kassa"] += jain_arvo
      muutos_tehty = True
      send_telegram_message(
          f"🛑 <b>BOTTI 2: STOP-LOSS LAUKESI ({pos['ticker']} Mini Long)</b>\n"
          f"Positio suljettu hintaan {current_price:.2f}."
      )

  if muutos_tehty:
    tallenna_salkku(salkku)


def paivita_salkku_html():
  salkku = lataa_salkku()

  avoimet_arvo = 0
  for pos in salkku["positiot"]:
    if pos["tila"] == "AVOIN":
      try:
        ticker = yf.Ticker(pos["ticker"])
        hinta_nyt = ticker.history(period="1d")["Close"].iloc[-1]
        avoimet_arvo += pos["panos"] * (hinta_nyt / pos["ostohinta"])
      except:
        avoimet_arvo += pos["panos"]

  kokonaissaldo = salkku["kassa"] + avoimet_arvo
  kokonaistuotto_eur = kokonaissaldo - salkku["alkupääoma"]
  kokonaistuotto_pct = ((kokonaistuotto_eur / salkku["alkupääoma"]) * 100) if salkku["alkupääoma"] > 0 else 0
  saldo_style = "pos" if kokonaistuotto_eur >= 0 else "neg"

  html_content = f"""
    <!DOCTYPE html>
    <html lang="fi">
    <head>
        <meta charset="UTF-8">
        <title>Botti 2 - Autonominen Mini Long Treidaussalkku</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
            .container {{ max-width: 1000px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
            h1 {{ color: #1a73e8; text-align: center; margin-bottom: 5px; }}
            .subtitle {{ text-align: center; color: #666; font-size: 0.9em; margin-bottom: 30px; }}
            .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 35px; }}
            .card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 5px solid #1a73e8; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
            .card h3 {{ margin: 0 0 10px 0; color: #555; font-size: 0.95em; }}
            .card p {{ margin: 0; font-size: 1.5em; font-weight: bold; color: #222; }}
            h2 {{ color: #333; border-bottom: 2px solid #eee; padding-bottom: 8px; margin-top: 30px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; margin-bottom: 30px; }}
            th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #e0e0e0; font-size: 0.95em; }}
            th {{ background-color: #f8f9fa; color: #444; font-weight: 600; }}
            .pos {{ color: #2e7d32; font-weight: bold; }}
            .neg {{ color: #c62828; font-weight: bold; }}
            .badge {{ background: #e3f2fd; color: #1565c0; padding: 4px 8px; border-radius: 4px; font-size: 0.85em; font-weight: 600; }}
            .footer {{ text-align: center; margin-top: 40px; font-size: 0.85em; color: #888; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Botti 2: Autonominen Treidaussalkku (Mini Long)</h1>
            <div class="subtitle">Alkupääoma: {salkku['alkupääoma']:.2f} € | Päivitetty (Suomen aika): {get_finland_time().strftime("%d.%m.%Y klo %H:%M")}</div>
            
            <div class="summary-grid">
                <div class="card">
                    <h3>Kokonaissaldo</h3>
                    <p>{kokonaissaldo:.2f} €</p>
                </div>
                <div class="card" style="border-left-color: #2e7d32;">
                    <h3>Käteiskassa</h3>
                    <p>{salkku['kassa']:.2f} €</p>
                </div>
                <div class="card" style="border-left-color: #ff9800;">
                    <h3>Kokonaistuotto</h3>
                    <p class="{saldo_style}">{'+' if kokonaistuotto_eur >= 0 else ''}{kokonaistuotto_eur:.2f} € ({'+' if kokonaistuotto_pct >= 0 else ''}{kokonaistuotto_pct:.2f}%)</p>
                </div>
            </div>

            <h2>Käynnissä olevat treidit (Avoimet Mini Long -positiot)</h2>
            <table>
                <tr>
                    <th>Kohde / Tyyppi</th>
                    <th>Avauspvm</th>
                    <th>Panos</th>
                    <th>Sisääntulo</th>
                    <th>Nykyhinta</th>
                    <th>Tuotto (€ / %)</th>
                    <th>Stop-Loss / Tavoite</th>
                    <th>Tila</th>
                </tr>
    """

  avoimia_löytyy = False
  for pos in salkku["positiot"]:
    if pos["tila"] == "AVOIN":
      avoimia_löytyy = True
      try:
        ticker_obj = yf.Ticker(pos["ticker"])
        nykyhinta = ticker_obj.history(period="1d")["Close"].iloc[-1]
      except:
        nykyhinta = pos["ostohinta"]

      # Lasketaan tuotto
      positio_arvo_nyt = pos["panos"] * (nykyhinta / pos["ostohinta"])
      tuotto_eur = positio_arvo_nyt - pos["panos"]
      tuotto_pct = ((nykyhinta - pos["ostohinta"]) / pos["ostohinta"]) * 100
      tuotto_style = "pos" if tuotto_eur >= 0 else "neg"

      tila_teksti = "Aktiivinen (Osavoitto otettu)" if pos["osavoitto_kotiutettu"] else "Aktiivinen"
      html_content += f"""
                <tr>
                    <td><b>{pos['ticker']}</b><br><span style="font-size: 0.8em; color: #666;">Nordnet Mini Long</span></td>
                    <td>{pos['pvm']}</td>
                    <td>{pos['panos']:.2f} €</td>
                    <td>{pos['ostohinta']:.2f} €</td>
                    <td><b>{nykyhinta:.2f} €</b></td>
                    <td><span class="{tuotto_style}">{'+' if tuotto_eur >= 0 else ''}{tuotto_eur:.2f} € ({'+' if tuotto_pct >= 0 else ''}{tuotto_pct:.2f}%)</span></td>
                    <td><span class="neg">SL: {pos['stop_loss']:.2f} €</span><br><span class="pos">TP: {pos['tavoite']:.2f} €</span></td>
                    <td><span class="badge">{tila_teksti}</span></td>
                </tr>
    """

  if not avoimia_löytyy:
    html_content += "<tr><td colspan='8' style='text-align: center; color: #777;'>Ei avoimia positioita tällä hetkellä.</td></tr>"

  html_content += """
            </table>

            <h2>Tehdyt treidit (Historia)</h2>
            <table>
                <tr>
                    <th>Kohde</th>
                    <th>Avauspvm</th>
                    <th>Alkupanos</th>
                    <th>Päätöstila</th>
                </tr>
    """

  historiaa_löytyy = False
  for pos in salkku["positiot"]:
    if pos["tila"] != "AVOIN":
      historiaa_löytyy = True
      html_content += f"""
                <tr>
                    <td><b>{pos['ticker']}</b></td>
                    <td>{pos['pvm']}</td>
                    <td>{pos['panos']:.2f} €</td>
                    <td><span style="color: #666;">Suljettu (Stop-Loss / Tulos)</span></td>
                </tr>
    """

  if not historiaa_löytyy:
    html_content += "<tr><td colspan='4' style='text-align: center; color: #777;'>Ei vielä suljettuja treidejä.</td></tr>"

  html_content += f"""
            </table>
            <div class="footer">Autonominen Mini Long Treidausbotti &bull; Generoitu automaattisesti GitHub Actionsilla</div>
        </div>
    </body>
    </html>
    """

  with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
  print("Botti 2: Salkun HTML-sivu päivitetty reaaliaikaisilla tuotoilla!")


# --- PÄÄOHJELMA ---
def main():
  print("--- Botti 1: Fundamentti- ja dippivahti käynnissä ---")
  lahetetyt_viikolla = lue_historia()

  for ticker in TICKERS:
    try:
      current_price, sma200, analyysi = botti_1_analysoi(ticker)
      if current_price and analyysi:
        current_week = get_finland_time().strftime("%Y-W%V")
        if f"{ticker}_{current_week}" not in lahetetyt_viikolla:
          print(f"[{ticker}] Botti 1 hyväksyi kohteen uutis- ja teknisellä seulalla!")
          botti_2_tee_sijoituspaatos(ticker, current_price, sma200, analyysi)
          tallenna_historia(ticker)
    except Exception as e:
      print(f"Virhe osakkeen {ticker} käsittelyssä: {e}")

  print("\n--- Botti 2: Päivitetään avoimet treidit & HTML-sivu ---")
  paivita_avoimet_treidit()
  paivita_salkku_html()


if __name__ == "__main__":
  main()
