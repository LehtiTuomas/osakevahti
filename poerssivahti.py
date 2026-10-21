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

# Seurantalista (98 kohdetta)
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


# --- BOTTI 1: TIUKENNETTU VIIKKOMUISTI, UUTISET JA DIPPIVAHTI ---
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

  # TIUKENNETTU SEULA: Vaaditaan syvempi dippi (-2%) tai matalampi RSI (<= 40), 
  # ja hinnan on oltava selvästi nousutrendissä (yli 97% SMA200:sta)
  is_dipping = day_change_pct <= -2.0
  is_rsi_low = current_rsi <= 40
  is_in_long_term_uptrend = current_price > (sma200 * 0.97)

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
Olet erittäin kriittinen ja kokenut osakeanalyytikko. Tarkista, onko kyseessä poikkeuksellisen laadukas dippi vahvassa nousutrendissä. Hylkää herkästi jos uutisissa on vähänkään epäilyttävää.
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
      temperature=0.1,
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


def laske_kokonaissaldo(salkku):
  avoimet_arvo = 0
  for pos in salkku["positiot"]:
    if pos["tila"] == "AVOIN":
      try:
        ticker = yf.Ticker(pos["ticker"])
        hinta_nyt = ticker.history(period="1d")["Close"].iloc[-1]
      except:
        hinta_nyt = pos["ostohinta"]
      vipu = pos.get("vipu", 1)
      hinnan_muutos_pct = (hinta_nyt - pos["ostohinta"]) / pos["ostohinta"]
      jella_oleva_osuus = 0.5 if pos["osavoitto_kotiutettu"] else 1.0
      vivutettu_arvo = (pos["panos"] * jella_oleva_osuus) * (1 + (hinnan_muutos_pct * vipu))
      avoimet_arvo += max(0, vivutettu_arvo)
  return salkku["kassa"] + avoimet_arvo


def botti_2_tee_sijoituspaatos(ticker_symbol, current_price, sma200, botti_1_analyysi):
  salkku = lataa_salkku()

  for pos in salkku["positiot"]:
    if pos["ticker"] == ticker_symbol and pos["tila"] == "AVOIN":
      return

  kokonaissaldo = laske_kokonaissaldo(salkku)
  max_risk_eur = kokonaissaldo * 0.10  # Maksimiriski 10% salkun arvosta
  max_mahdollinen_panos = min(salkku["kassa"], kokonaissaldo * 0.90)

  if max_mahdollinen_panos < 100:
    print(f"Käteiskassa liian pieni uuteen treidiin (< 100 €)")
    return

  client = Groq(api_key=GROQ_API_KEY)
  prompt = f"""
Olet itsenäinen, kylmäverinen viputuotteiden (Nordnet Mini Long) treidausbotti. 
Salkun kokonaisarvo on {kokonaissaldo:.2f} €. Käteiskassa on {salkku['kassa']:.2f} €. 
Maksimiriski tälle treidille saa olla enintään {max_risk_eur:.2f} € (10% salkusta).
Sijoitettava panos saa olla vähintään 100 € ja enintään {max_mahdollinen_panos:.2f} €.
Päätä itse sijoitettava panos, vipu (1-10), Stop-Loss ja Take Profit.

Botti 1 analyysi: "{botti_1_analyysi}"
Kohteen tiedot: {ticker_symbol} | Hinta: {current_price:.2f} | Tukitaso (SMA200): {sma200:.2f}

Tee itsenäinen päätös JSON-muodossa ilman markdown-tageja:
{{
  "sijoita": true,
  "panos": 500.0,
  "vipu": 4,
  "perustelu": "Perustelu panokselle, vivulle ja kaupalle",
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
      panos = float(data.get("panos", 100.0))
      # Varmistetaan ehdot: vähintään 100€ ja enintään kassa
      panos = max(100.0, min(panos, salkku["kassa"]))
      
      vipu = int(data.get("vipu", 3))
      vipu = max(1, min(10, vipu))

      uusipositio = {
          "id": len(salkku["positiot"]) + 1,
          "ticker": ticker_symbol,
          "tila": "AVOIN",
          "vipu": vipu,
          "ostohinta": data["sisääntulohinta"],
          "panos": panos,
          "stop_loss": data["stop_loss"],
          "tavoite": data["ensimmainen_tukitaso_tavoite"],
          "pvm": get_finland_time().strftime("%Y-%m-%d"),
          "osavoitto_kotiutettu": False,
      }
      salkku["kassa"] -= panos
      salkku["positiot"].append(uusipositio)
      tallenna_salkku(salkku)

      viesti = (
          f"🤖 <b>BOTTI 2: UUSI MINI LONG AVATTU (Vipu x{vipu})</b> 🤖\n"
          f"- Kohde: {ticker_symbol}\n"
          f"- Panos: {panos:.2f} € | Vipu: x{vipu}\n"
          f"- Perustelu: {data['perustelu']}\n"
          f"- Stop-Loss: {data['stop_loss']:.2f}\n"
          f"- Take Profit: {data['ensimmainen_tukitaso_tavoite']:.2f}"
      )
      send_telegram_message(viesti)
  except Exception as e:
    print(f"Botti 2 päätösvirhe: {e}")


def paivita_avoimet_treidit():
  salkku = lataa_salkku()
  muutos_tehty = False
  client = Groq(api_key=GROQ_API_KEY)

  for pos in salkku["positiot"]:
    if pos["tila"] != "AVOIN":
      continue
    try:
      ticker = yf.Ticker(pos["ticker"])
      hist = ticker.history(period="2d")
      if hist.empty:
        continue
      paivan_alin = hist["Low"].iloc[-1]
      paivan_ylin = hist["High"].iloc[-1]
      current_price = hist["Close"].iloc[-1]
    except:
      continue

    vipu = pos.get("vipu", 1)

    # 1. Stop-Loss
    if paivan_alin <= pos["stop_loss"]:
      pos["tila"] = "SULJETTU"
      hinnan_muutos_pct = (pos["stop_loss"] - pos["ostohinta"]) / pos["ostohinta"]
      vivutettu_tulos_pct = hinnan_muutos_pct * vipu
      jella_oleva_osuus = 0.5 if pos["osavoitto_kotiutettu"] else 1.0
      loppuarvo = max(0, (pos["panos"] * jella_oleva_osuus) * (1 + vivutettu_tulos_pct))
      salkku["kassa"] += loppuarvo
      muutos_tehty = True
      send_telegram_message(
          f"🛑 <b>BOTTI 2: STOP-LOSS LAUKESI ({pos['ticker']} Vipu x{vipu})</b>\n"
          f"Päivän alin ({paivan_alin:.2f}) alitti stop-lossin ({pos['stop_loss']:.2f}). Positio suljettu."
      )
      continue

    # 2. Take Profit (Osavoitto)
    if not pos["osavoitto_kotiutettu"] and paivan_ylin >= pos["tavoite"]:
      pos["osavoitto_kotiutettu"] = True
      hinnan_muutos_pct = (pos["tavoite"] - pos["ostohinta"]) / pos["ostohinta"]
      vivutettu_tulos_pct = hinnan_muutos_pct * vipu
      myyty_osuus = (pos["panos"] / 2) * (1 + vivutettu_tulos_pct)
      salkku["kassa"] += myyty_osuus
      pos["stop_loss"] = pos["ostohinta"]
      muutos_tehty = True
      send_telegram_message(
          f"🎯 <b>BOTTI 2: OSAVOITTO KOTIUTETTU ({pos['ticker']} Vipu x{vipu})</b>\n"
          f"Päivän ylin saavutti tavoitteen ({pos['tavoite']:.2f}). Myyty 50%, stop-loss nostettu sisäänhintaan!"
      )
      continue

    # 3. Tekoälyn myyntipäätös lopuille
    try:
      uutis_teksti = hae_uutiset_ja_keskustelut(ticker)
      hinnan_muutos_nyt = ((current_price - pos["ostohinta"]) / pos["ostohinta"]) * 100 * vipu

      prompt = f"""
Olet autonominen treidausbotti. Hallinnoit avointa Mini Long -positiota kohteessa {pos['ticker']}.
- Sisääntulohinta: {pos['ostohinta']:.2f} | Nykyhinta: {current_price:.2f} | Vipu: x{vipu}
- Vivutettu tuotto/tappio: {hinnan_muutos_nyt:.2f}%
- Osavoitto kotiutettu: {pos['osavoitto_kotiutettu']}
- Uutiset: "{uutis_teksti}"

Päätä, pitäisikö positio SULKEA NYT (myydä loputkin pois) vai PITÄÄ AVOIMENA.

Vastaa TÄSMÄLLEEN JSON-muodossa ilman markdown-tageja:
{{
  "sulje_positio": true tai false,
  "perustelu": "Lyhyt syy miksi myydään tai pidetään"
}}
"""
      ai_response = client.chat.completions.create(
          model="openai/gpt-oss-120b",
          messages=[{"role": "user", "content": prompt}],
          temperature=0.1,
      )
      res_text = ai_response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
      ai_decision = json.loads(res_text)

      if ai_decision.get("sulje_positio"):
        pos["tila"] = "SULJETTU"
        hinnan_muutos_pct = (current_price - pos["ostohinta"]) / pos["ostohinta"]
        vivutettu_tulos_pct = hinnan_muutos_pct * vipu
        jella_oleva_osuus = 0.5 if pos["osavoitto_kotiutettu"] else 1.0
        loppuarvo = max(0, (pos["panos"] * jella_oleva_osuus) * (1 + vivutettu_tulos_pct))
        salkku["kassa"] += loppuarvo
        muutos_tehty = True

        send_telegram_message(
            f"🤖 <b>BOTTI 2: TEKOÄLY SULKI POSITION ({pos['ticker']} Vipu x{vipu})</b>\n"
            f"- Hinta: {current_price:.2f}\n"
            f"- Perustelu: {ai_decision.get('perustelu')}"
        )
    except Exception as e:
      print(f"Virhe tekoälyn myyntipäätöksessä: {e}")

  if muutos_tehty:
    tallenna_salkku(salkku)


def paivita_salkku_html():
  salkku = lataa_salkku()
  kokonaissaldo = laske_kokonaissaldo(salkku)
  kokonaistuotto_eur = kokonaissaldo - salkku["alkupääoma"]
  kokonaistuotto_pct = ((kokonaistuotto_eur / salkku["alkupääoma"]) * 100) if salkku["alkupääoma"] > 0 else 0
  saldo_style = "pos" if kokonaistuotto_eur >= 0 else "neg"

  html_content = f"""
    <!DOCTYPE html>
    <html lang="fi">
    <head>
        <meta charset="UTF-8">
        <title>Botti 2 - Autonominen Viputreidaussalkku</title>
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
                    <p class="{saldo_style}">{'+' if kokonaissaldo >= salkku['alkupääoma'] else ''}{kokonaistuotto_eur:.2f} € ({'+' if kokonaistuotto_pct >= 0 else ''}{kokonaistuotto_pct:.2f}%)</p>
                </div>
            </div>

            <h2>Käynnissä olevat treidit (Avoimet Mini Long -positiot)</h2>
            <table>
                <tr>
                    <th>Kohde / Vipu</th>
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

      vipu = pos.get("vipu", 1)
      hinnan_muutos_pct = (nykyhinta - pos["ostohinta"]) / pos["ostohinta"]
      vivutettu_tuotto_pct = hinnan_muutos_pct * vipu * 100
      jella_oleva_osuus = 0.5 if pos["osavoitto_kotiutettu"] else 1.0
      tuotto_eur = (pos["panos"] * jella_oleva_osuus) * (hinnan_muutos_pct * vipu)
      tuotto_style = "pos" if tuotto_eur >= 0 else "neg"

      tila_teksti = "Aktiivinen (Osavoitto otettu)" if pos["osavoitto_kotiutettu"] else "Aktiivinen"
      html_content += f"""
                <tr>
                    <td><b>{pos['ticker']}</b><br><span class="badge">Vipu x{vipu}</span></td>
                    <td>{pos['pvm']}</td>
                    <td>{pos['panos']:.2f} €</td>
                    <td>{pos['ostohinta']:.2f} €</td>
                    <td><b>{nykyhinta:.2f} €</b></td>
                    <td><span class="{tuotto_style}">{'+' if tuotto_eur >= 0 else ''}{tuotto_eur:.2f} € ({'+' if vivutettu_tuotto_pct >= 0 else ''}{vivutettu_tuotto_pct:.2f}%)</span></td>
                    <td><span class="neg">SL: {pos['stop_loss']:.2f} €</span><br><span class="pos">TP: {pos['tavoite']:.2f} €</span></td>
                    <td><span class="badge" style="background: #e8f5e9; color: #2e7d32;">{tila_teksti}</span></td>
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
                    <td><span style="color: #666;">Suljettu (Stop-Loss / Tulos / Tekoäly)</span></td>
                </tr>
    """

  if not historiaa_löytyy:
    html_content += "<tr><td colspan='4' style='text-align: center; color: #777;'>Ei vielä suljettuja treidejä.</td></tr>"

  html_content += f"""
            </table>
            <div class="footer">Autonominen Viputreidausbotti &bull; Generoitu automaattisesti GitHub Actionsilla</div>
        </div>
    </body>
    </html>
    """

  with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
  print("Botti 2: Salkun HTML-sivu päivitetty!")


# --- PÄÄOHJELMA ---
def main():
  print("--- Botti 1: Tiukennettu dippivahti käynnissä ---")
  lahetetyt_viikolla = lue_historia()

  for ticker in TICKERS:
    try:
      current_price, sma200, analyysi = botti_1_analysoi(ticker)
      if current_price and analyysi:
        current_week = get_finland_time().strftime("%Y-W%V")
        if f"{ticker}_{current_week}" not in lahetetyt_viikolla:
          print(f"[{ticker}] Botti 1 hyväksyi kohteen tiukalla seulalla!")
          botti_2_tee_sijoituspaatos(ticker, current_price, sma200, analyysi)
          tallenna_historia(ticker)
    except Exception as e:
      print(f"Virhe osakkeen {ticker} käsittelyssä: {e}")

  print("\n--- Botti 2: Päivitetään avoimet treidit & HTML-sivu ---")
  paivita_avoimet_treidit()
  paivita_salkku_html()


if __name__ == "__main__":
  main()
  main()
