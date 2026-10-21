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
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram-viestin lähetysvirhe: {e}")

# --- BOTTI 1: TIUKENNETTU VIIKKOMUISTI, FUNDAMENTTI- JA DIPPIVAHTI ---
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
            for item in news_list[:7]:
                title = item.get("content", {}).get("title") or item.get("title", "")
                publisher = item.get("content", {}).get("provider", {}).get("displayName", "")
                if title:
                    if publisher:
                        otsikot.append(f"[{publisher}] {title}")
                    else:
                        otsikot.append(title)
        return " | ".join(otsikot) if otsikot else "Ei tuoreita uutisia saatavilla, tekninen liike."
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
Olet kriittinen osake- ja fundamenttianalyytikko.
Tarkista, onko osakkeessa menossa terveellinen dippi nousutrendissä VAI fundamentaalinen ongelma.

OSAKE: {ticker_symbol}
- Hinta: {current_price:.2f} | Päivämuutos: {day_change_pct:.2f}% | RSI: {current_rsi:.1f}
- UUTISET / TULOSJULKAISUT: "{uutis_teksti}"

Vastaa TÄSMÄLLEEN muodossa:
PÄÄTÖS: [HYVÄKSYTTY tai HYLÄTTY]
PERUSTELU: [1-2 lauseen fundamenttianalyysi uutisista ja taustasta]
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
            jella_oleva_osuus = 0.5 if pos.get("osavoitto_kotiutettu") else 1.0
            vivutettu_arvo = (pos["panos"] * jella_oleva_osuus) * (1 + (hinnan_muutos_pct * vipu))
            avoimet_arvo += max(0, vivutettu_arvo)
    return salkku["kassa"] + avoimet_arvo

def botti_2_tee_sijoituspaatos(ticker_symbol, current_price, sma200, botti_1_analyysi):
    salkku = lataa_salkku()

    for pos in salkku["positiot"]:
        if pos["ticker"] == ticker_symbol and pos["tila"] == "AVOIN":
            return

    kokonaissaldo = laske_kokonaissaldo(salkku)
    max_risk_eur = kokonaissaldo * 0.10
    max_mahdollinen_panos = min(salkku["kassa"], kokonaissaldo * 0.90)

    if max_mahdollinen_panos < 100:
        print(f"Käteiskassa liian pieni uuteen treidiin (< 100 €)")
        return

    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""
Olet ammattimainen ja kurinalainen viputuotteiden (Nordnet Mini Long) swing-treidausbotti. 

STRATEGINEN TAVOITE:
- Pyrit siihen, että vähintään 2/3 (n. 67 %) avaamistasi treideistä on voittavia.
- Maksimoit salkun kokonaistuoton optimoimalla risk/reward-suhteen jokaiselle kaupalle.

SALKUN TILA & RAJOITUKSET:
- Salkun kokonaisarvo: {kokonaissaldo:.2f} € | Käteiskassa: {salkku['kassa']:.2f} €
- Maksimiriski (Stop-Loss -tappio): enintään {max_risk_eur:.2f} € (10% salkusta)
- Sijoitettava panos: vähintään 100.00 € ja enintään {max_mahdollinen_panos:.2f} €

ANALYYSITIEDOT:
- Botti 1 fundamenttianalyysi: "{botti_1_analyysi}"
- Kohde: {ticker_symbol} | Hinta: {current_price:.2f} | SMA200-tuki: {sma200:.2f}

Tee sijoituspäätös JSON-muodossa ilman markdown-tageja:
{{
  "sijoita": true,
  "panos": 500.0,
  "vipu": 4,
  "perustelu": "Ytimekäs analyysi miksi osake ostettiin ja miksi valittiin tämä panos/vipu",
  "sisääntulohinta": {current_price},
  "stop_loss": {round(current_price * 0.95, 2)},
  "ensimmainen_tukitaso_tavoite": {round(current_price * 1.06, 2)}
}}
Jos paikka ei täytä 2/3-voittotavoitteen kriteereitä, aseta "sijoita": false.
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
            panos = max(100.0, min(panos, salkku["kassa"]))
            vipu = max(1, min(10, int(data.get("vipu", 3))))

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
                "aloitus_perustelu": data.get("perustelu", "Teknisen ja fundamenttianalyysin perusteella valittu kohde."),
                "toteutunut_tulos_eur": 0.0,
                "toteutunut_tulos_pct": 0.0,
                "sulkusyy": ""
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

        # 1. Stop-Loss tarkistus
        if paivan_alin <= pos["stop_loss"]:
            pos["tila"] = "SULJETTU"
            hinnan_muutos_pct = (pos["stop_loss"] - pos["ostohinta"]) / pos["ostohinta"]
            vivutettu_tulos_pct = hinnan_muutos_pct * vipu
            jella_oleva_osuus = 0.5 if pos.get("osavoitto_kotiutettu") else 1.0
            panos_osa = pos["panos"] * jella_oleva_osuus
            loppuarvo = max(0, panos_osa * (1 + vivutettu_tulos_pct))
            
            tulos_eur = loppuarvo - panos_osa
            pos["toteutunut_tulos_eur"] = pos.get("toteutunut_tulos_eur", 0.0) + tulos_eur
            pos["toteutunut_tulos_pct"] = vivutettu_tulos_pct * 100
            pos["sulkusyy"] = f"Stop-Loss laukesi (Päivän alin {paivan_alin:.2f} € alitti tason {pos['stop_loss']:.2f} €)"

            salkku["kassa"] += loppuarvo
            muutos_tehty = True
            send_telegram_message(
                f"🛑 <b>BOTTI 2: STOP-LOSS LAUKESI ({pos['ticker']} Vipu x{vipu})</b>\n"
                f"Päivän alin ({paivan_alin:.2f}) osui stop-lossiin ({pos['stop_loss']:.2f}). Positio suljettu."
            )
            continue

        # 2. Take Profit (Osavoitto)
        if not pos.get("osavoitto_kotiutettu") and paivan_ylin >= pos["tavoite"]:
            pos["osavoitto_kotiutettu"] = True
            hinnan_muutos_pct = (pos["tavoite"] - pos["ostohinta"]) / pos["ostohinta"]
            vivutettu_tulos_pct = hinnan_muutos_pct * vipu
            myyty_osuus_panoksesta = pos["panos"] / 2
            myyty_arvo = myyty_osuus_panoksesta * (1 + vivutettu_tulos_pct)
            
            tulos_eur = myyty_arvo - myyty_osuus_panoksesta
            pos["toteutunut_tulos_eur"] = pos.get("toteutunut_tulos_eur", 0.0) + tulos_eur
            pos["stop_loss"] = pos["ostohinta"]

            salkku["kassa"] += myyty_arvo
            muutos_tehty = True
            send_telegram_message(
                f"🎯 <b>BOTTI 2: OSAVOITTO KOTIUTETTU ({pos['ticker']} Vipu x{vipu})</b>\n"
                f"Päivän ylin saavutti tavoitteen ({pos['tavoite']:.2f}). Myyty 50%, stop-loss nostettu sisäänhintaan!"
            )
            continue

        # 3. TEKOÄLYN SULKUPÄÄTÖS (Uutisriskit tai käänne)
        try:
            uutis_teksti = hae_uutiset_ja_keskustelut(ticker)
            hinnan_muutos_nyt = ((current_price - pos["ostohinta"]) / pos["ostohinta"]) * 100 * vipu

            prompt = f"""
Olet kurinalainen swing-treidaaja. Hallinnoit avointa Mini Long -positiota kohteessa {pos['ticker']}.
- Sisääntulohinta: {pos['ostohinta']:.2f} | Nykyhinta: {current_price:.2f} | Vipu: x{vipu}
- Vivutettu tuotto/tappio: {hinnan_muutos_nyt:.2f}%
- Osavoitto kotiutettu: {pos.get('osavoitto_kotiutettu')}
- Uutiset: "{uutis_teksti}"

SÄÄNTÖ: Oletusarvoisesti anna treidin juosta Stop-Lossiin tai Take Profitiin asti! 
Sulje positio ENNENAIKAISESTI VAIN, JOS uutisissa näkyy VAKAVA UUTISRISKI tai FUNDAMENTAALINEN KÄÄNNE.

Vastaa JSON-muodossa ilman markdown-tageja:
{{
  "sulje_positio": false,
  "perustelu": "Lyhyt syy myynnille tai pitämiselle"
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
                jella_oleva_osuus = 0.5 if pos.get("osavoitto_kotiutettu") else 1.0
                panos_osa = pos["panos"] * jella_oleva_osuus
                loppuarvo = max(0, panos_osa * (1 + vivutettu_tulos_pct))

                tulos_eur = loppuarvo - panos_osa
                pos["toteutunut_tulos_eur"] = pos.get("toteutunut_tulos_eur", 0.0) + tulos_eur
                pos["toteutunut_tulos_pct"] = vivutettu_tulos_pct * 100
                pos["sulkusyy"] = f"Tekoälyn ennenaikainen myynti: {ai_decision.get('perustelu')}"

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
            * {{ box-sizing: border-box; }}
            body {{ font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
            .container {{ max-width: 1200px; margin: auto; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
            h1 {{ color: #1a73e8; text-align: center; margin-bottom: 5px; font-size: 1.8em; }}
            .subtitle {{ text-align: center; color: #666; font-size: 0.88em; margin-bottom: 20px; }}
            
            .strategy-box {{ background: #eef5fe; border: 1px solid #d0e1fd; border-left: 5px solid #1a73e8; padding: 15px 18px; border-radius: 8px; margin-bottom: 25px; font-size: 0.88em; color: #2c3e50; line-height: 1.5; }}
            .strategy-box h4 {{ margin: 0 0 6px 0; color: #1a73e8; font-size: 1.05em; display: flex; align-items: center; gap: 6px; }}
            .strategy-box ul {{ margin: 6px 0 0 0; padding-left: 20px; }}
            .strategy-box li {{ margin-bottom: 4px; }}

            .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 30px; }}
            .card {{ background: #f8f9fa; padding: 18px; border-radius: 8px; border-left: 5px solid #1a73e8; box-shadow: 0 2px 4px rgba(0,0,0,0.02); }}
            .card h3 {{ margin: 0 0 5px 0; color: #555; font-size: 0.85em; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
            .card p {{ margin: 0; font-size: 1.4em; font-weight: bold; color: #222; }}
            
            h2 {{ color: #333; border-bottom: 2px solid #eee; padding-bottom: 8px; margin-top: 30px; font-size: 1.25em; }}
            
            .table-responsive {{ width: 100%; overflow-x: auto; margin-bottom: 30px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }}
            th, td {{ padding: 12px 10px; text-align: left; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
            th {{ background-color: #f8f9fa; color: #444; font-weight: 600; white-space: nowrap; }}
            
            .nowrap {{ white-space: nowrap; }}
            .pos {{ color: #2e7d32; font-weight: bold; }}
            .neg {{ color: #c62828; font-weight: bold; }}
            .badge {{ background: #e3f2fd; color: #1565c0; padding: 3px 6px; border-radius: 4px; font-size: 0.8em; font-weight: 600; display: inline-block; white-space: nowrap; }}
            .reason-box {{ font-size: 0.85em; color: #555; background: #fafafa; padding: 8px 10px; border-radius: 6px; border: 1px solid #eee; line-height: 1.35; }}
            .footer {{ text-align: center; margin-top: 35px; font-size: 0.8em; color: #888; }}

            /* MOBIILINÄKYMÄ (Puhelimen ruudulla korttinäkymä) */
            @media screen and (max-width: 768px) {{
                body {{ padding: 10px; }}
                .container {{ padding: 15px; }}
                h1 {{ font-size: 1.4em; }}
                
                table, thead, tbody, th, td, tr {{ display: block; }}
                thead {{ display: none; }}
                
                tr {{ margin-bottom: 15px; border: 1px solid #e0e0e0; border-radius: 10px; padding: 12px; background: #fff; box-shadow: 0 2px 5px rgba(0,0,0,0.03); }}
                td {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px dashed #eee; font-size: 0.88em; }}
                td:last-child {{ border-bottom: none; flex-direction: column; align-items: flex-start; gap: 4px; padding-top: 10px; }}
                
                td::before {{ content: attr(data-label); font-weight: 600; color: #666; margin-right: 15px; flex-shrink: 0; }}
                .nowrap {{ white-space: normal; }}
                .reason-box {{ width: 100%; margin-top: 4px; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Botti 2: Autonominen Treidaussalkku (Mini Long)</h1>
            <div class="subtitle">Alkupääoma: {salkku['alkupääoma']:.2f} € | Päivitetty: {get_finland_time().strftime("%d.%m.%Y klo %H:%M")}</div>
            
            <div class="strategy-box">
                <h4>📊 Osakevalintastrategia & Kriteerit</h4>
                Salkkuun hyväksytään vain poikkeuksellisen vahvat swing-paikat Nordnet Mini Long -tuotteilla noudattaen näitä sääntöjä:
                <ul>
                    <li><b>Tekninen nousutrendi:</b> Osakkeen nykyhinnan tulee olla vahvassa pitkän aikavälin nousutrendissä eli <b>yli 97% suhteessa 200 päivän liukuvaan keskiarvoon (SMA200)</b>.</li>
                    <li><b>Dippi / Ylilyönti:</b> Mukaan haetaan tilapäistä korjausliikettä, eli päiväkohtainen lasku on <b>vähintään -2.0%</b> tai 14 päivän <b>RSI on ≤ 40</b>.</li>
                    <li><b>Momentumin vakautuminen:</b> Viimeisen 4 tunnin kynttilöiden perusteella varmistetaan, ettei kurssi ole vapaassa pudotuksessa ostohetkellä.</li>
                    <li><b>Fundamentti- ja uutissuodatin:</b> Groq-tekoäly lukee tuoreet uutiset ja tulosjulkaisutiedotteet. Dippi hylätään heti, jos taustalla on fundamentaalinen ongelma (kuten tulosvaroitus tai strateginen kriisi).</li>
                    <li><b>2/3 Voittotavoite & Riskienhallinta:</b> Botti 2 valitsee viputason (1–10x) ja sijoituspanoksen (vähintään 100 €) siten, että kunkin kaupan Stop-Loss -maksimitappio on enintään 10% salkun kokonaisarvosta ja risk/reward-suhde tukee vähintään ~67% voittoprosenttia.</li>
                </ul>
            </div>

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
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Kohde / Vipu</th>
                            <th>Avauspvm</th>
                            <th>Panos</th>
                            <th>Sisääntulo</th>
                            <th>Nykyhinta</th>
                            <th>Tuotto (€ / %)</th>
                            <th>Stop-Loss / Tavoite</th>
                            <th>Ostoperustelu (Botti 1 & 2)</th>
                        </tr>
                    </thead>
                    <tbody>
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
            jella_oleva_osuus = 0.5 if pos.get("osavoitto_kotiutettu") else 1.0
            tuotto_eur = (pos["panos"] * jella_oleva_osuus) * (hinnan_muutos_pct * vipu)
            tuotto_style = "pos" if tuotto_eur >= 0 else "neg"

            perustelu = pos.get("aloitus_perustelu", "Ei kirjattua perustelua.")
            html_content += f"""
                <tr>
                    <td data-label="Kohde / Vipu"><b>{pos['ticker']}</b> <span class="badge">Vipu x{vipu}</span></td>
                    <td data-label="Avauspvm" class="nowrap">{pos['pvm']}</td>
                    <td data-label="Panos" class="nowrap">{pos['panos']:.2f} €</td>
                    <td data-label="Sisääntulo" class="nowrap">{pos['ostohinta']:.2f} €</td>
                    <td data-label="Nykyhinta" class="nowrap"><b>{nykyhinta:.2f} €</b></td>
                    <td data-label="Tuotto (€ / %)" class="nowrap"><span class="{tuotto_style}">{'+' if tuotto_eur >= 0 else ''}{tuotto_eur:.2f} € ({'+' if vivutettu_tuotto_pct >= 0 else ''}{vivutettu_tuotto_pct:.2f}%)</span></td>
                    <td data-label="SL / TP" class="nowrap"><span class="neg">SL: {pos['stop_loss']:.2f} €</span><br><span class="pos">TP: {pos['tavoite']:.2f} €</span></td>
                    <td data-label="Perustelu"><div class="reason-box">{perustelu}</div></td>
                </tr>
    """

    if not avoimia_löytyy:
        html_content += "<tr><td colspan='8' style='text-align: center; color: #777;'>Ei avoimia positioita tällä hetkellä.</td></tr>"

    html_content += """
                    </tbody>
                </table>
            </div>

            <h2>Tehdyt treidit (Historia)</h2>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Kohde / Vipu</th>
                            <th>Avauspvm</th>
                            <th>Alkupanos</th>
                            <th>Toteutunut Tulos (€ / %)</th>
                            <th>Ostoperustelu & Sulkusyy</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    historiaa_löytyy = False
    for pos in salkku["positiot"]:
        if pos["tila"] != "AVOIN":
            historiaa_löytyy = True
            tulos_eur = pos.get("toteutunut_tulos_eur", 0.0)
            tulos_pct = pos.get("toteutunut_tulos_pct", 0.0)
            tulos_style = "pos" if tulos_eur >= 0 else "neg"
            
            sulkusyy = pos.get("sulkusyy", "Suljettu")
            aloitus_perustelu = pos.get("aloitus_perustelu", "Ei kirjattua perustelua.")

            html_content += f"""
                <tr>
                    <td data-label="Kohde / Vipu"><b>{pos['ticker']}</b> <span class="badge">Vipu x{pos.get('vipu', 1)}</span></td>
                    <td data-label="Avauspvm" class="nowrap">{pos['pvm']}</td>
                    <td data-label="Alkupanos" class="nowrap">{pos['panos']:.2f} €</td>
                    <td data-label="Tulos (€ / %)" class="nowrap"><span class="{tulos_style}">{'+' if tulos_eur >= 0 else ''}{tulos_eur:.2f} € ({'+' if tulos_pct >= 0 else ''}{tulos_pct:.2f}%)</span></td>
                    <td data-label="Perustelu / Sulku">
                        <div class="reason-box"><b>Osto:</b> {aloitus_perustelu}</div>
                        <div class="reason-box" style="margin-top:4px; border-left: 3px solid #666;"><b>Sulkusyy:</b> {sulkusyy}</div>
                    </td>
                </tr>
    """

    if not historiaa_löytyy:
        html_content += "<tr><td colspan='5' style='text-align: center; color: #777;'>Ei vielä suljettuja treidejä.</td></tr>"

    html_content += f"""
                    </tbody>
                </table>
            </div>
            <div class="footer">Autonominen Viputreidausbotti &bull; Generoitu automaattisesti GitHub Actionsilla</div>
        </div>
    </body>
    </html>
    """

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("Botti 2: Salkun HTML-sivu päivitetty selkeäksi tietokoneella ja mobiilissa!")

# --- PÄÄOHJELMA ---
def main():
    print("--- Botti 1: Tiukennettu dippi- ja fundamenttivahti käynnissä ---")
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
