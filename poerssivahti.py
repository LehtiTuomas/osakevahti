import json
import math
import os
import re
import time
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
BARIPVM_FILE = "viimeisin_baripvm.txt"   # muista committaa tämä Actionsissa kuten muutkin tiedostot

# --- STRATEGIAN SÄÄNNÖT (muuta tarvittaessa) ---
MAX_POSITIOITA = 4            # samanaikaisia positioita (avoimet + odottavat toimeksiannot)
MAX_PER_SEKTORI = 2
RISKI_PROSENTTI = 0.02        # max tappio / treidi = 2 % salkusta
MIN_VIPU = 1                  # tekoäly valitsee vivun väliltä MIN_VIPU..MAX_VIPU
MAX_VIPU = 10                 # (koodi rajaa sallitun maksimin SL-etäisyyden mukaan)
VIPU_OLETUS = 3               # jos tekoäly ei anna kelvollista vipua
KO_PUFFER = 0.5               # SL saa olla enintään 50 % matkasta Mini Longin knock-out-tasolle (≈ 1/vipu)
MIN_PANOS = 100.0
MAX_PANOS_OSUUS = 0.30        # yksittäinen panos enintään 30 % salkusta (matalalla vivulla panos kasvaisi muuten suureksi)
MAX_SL_ETAISYYS = 0.06        # SL max 6 % päässä sisäänhinnasta
MIN_RR = 2.0                  # R/R signaalihetkellä
MIN_RR_TOTEUTUS = 1.5         # R/R, kun todellinen avaushinta tiedetään (pieni gap sallitaan)
MIN_PISTEET = 7               # LLM:n fundamenttipisteet 1-10
TULOSRAJA_PAIVIA = 7          # ei avata, jos tulosjulkistus näin lähellä
KASSA_MIN_PROSENTTI = 0.20    # pidetään vähintään 20 % kassassa
TOIMEKSIANNON_VANHENEMINEN_PV = 7

FUNDAMENT_AVAIMET = {
    "sektori": "sector", "markkina_arvo": "marketCap",
    "forward_PE": "forwardPE", "trailing_PE": "trailingPE", "PEG": "pegRatio",
    "liikevaihdon_kasvu": "revenueGrowth", "EPS_kasvu": "earningsGrowth",
    "liikevoittomarginaali": "operatingMargins", "nettomarginaali": "profitMargins",
    "ROE": "returnOnEquity", "velka_per_oma_paaoma": "debtToEquity",
    "vapaa_kassavirta": "freeCashflow", "current_ratio": "currentRatio",
    "analyytikoiden_tavoitehinta": "targetMeanPrice",
    "analyytikoiden_suositus_1vahva_osta_5myy": "recommendationMean",
    "analyytikoita": "numberOfAnalystOpinions", "beta": "beta",
}


def get_finland_time():
    return datetime.now(ZoneInfo("Europe/Helsinki"))


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram-viestin lähetysvirhe: {e}")


# --- APUFUNKTIOT ---
def uusi_kaupankayntipaiva():
    """Yöajo: etsitään uusia signaaleja vain, jos S&P 500:n viimeisin päiväkynttilä on uusi.
    Estää saman perjantain datan käsittelyn uudelleen la/su, mutta sallii pe-illan ajon
    vaikka se tapahtuisi suomen ajassa lauantain puolella."""
    try:
        viimeisin = str(yf.Ticker("^GSPC").history(period="5d").index[-1].date())
    except Exception:
        return True
    edellinen = open(BARIPVM_FILE).read().strip() if os.path.exists(BARIPVM_FILE) else ""
    if viimeisin == edellinen:
        return False
    with open(BARIPVM_FILE, "w") as f:
        f.write(viimeisin)
    return True


def kutsu_llm(prompt, malli="openai/gpt-oss-120b", yrityksia=3):
    """Free tier: pieni tauko kutsujen välillä ja uudelleenyritys rate limitin yli."""
    for i in range(yrityksia):
        try:
            r = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                model=malli, messages=[{"role": "user", "content": prompt}], temperature=0.1)
            time.sleep(2)
            return r.choices[0].message.content
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                time.sleep(20 * (i + 1))
                continue
            raise
    return None


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else None


def sallittu_max_vipu(sl_osuus):
    """Mini Longin knock-out-taso on noin 1/vipu hinnan alapuolella. SL:n pitää jäädä selvästi
    sen sisäpuolelle: SL-etäisyys <= KO_PUFFER / vipu  =>  vipu <= KO_PUFFER / SL-etäisyys."""
    if sl_osuus <= 0:
        return MIN_VIPU
    return int(max(MIN_VIPU, min(MAX_VIPU, math.floor(KO_PUFFER / sl_osuus))))


# --- BOTTI 1: VIIKKOMUISTI, FUNDAMENTTI- JA DIPPIVAHTI ---
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


def hae_fundamentit(ticker_obj):
    try:
        info = ticker_obj.info or {}
    except Exception:
        info = {}
    return {k: info[v] for k, v in FUNDAMENT_AVAIMET.items() if info.get(v) is not None}


def hae_tulostiedot(ticker_obj):
    tulos = {"seuraava_tulospaiva": None, "paivia_tulokseen": None, "viimeiset_EPS_yllatykset_pct": []}
    today = get_finland_time().date()
    try:
        cal = ticker_obj.calendar
        pvmt = cal.get("Earnings Date") if isinstance(cal, dict) else None
        tulevat = [d for d in (pvmt or []) if d >= today]
        if tulevat:
            seur = min(tulevat)
            tulos["seuraava_tulospaiva"] = str(seur)
            tulos["paivia_tulokseen"] = (seur - today).days
    except Exception:
        pass
    try:
        ed = ticker_obj.get_earnings_dates(limit=8)
        if ed is not None and "Surprise(%)" in ed:
            tulos["viimeiset_EPS_yllatykset_pct"] = [round(x, 1) for x in ed["Surprise(%)"].dropna().head(4)]
    except Exception:
        pass
    return tulos


def laske_tasot(df, hinta):
    """SL ja TP lasketaan koodissa: ATR + swing low, TP vastustasosta. None jos R/R ei riitä."""
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    swing_low = df["Low"].tail(10).min()

    sl = min(hinta - 1.5 * atr, swing_low * 0.995)
    riski = hinta - sl
    if riski <= 0 or riski / hinta > MAX_SL_ETAISYYS:
        return None  # liian volatiili vivulle

    vastus = df["High"].tail(120).max()           # lähin merkittävä vastustaso
    tp = min(hinta + 3 * riski, vastus)
    palkinto = tp - hinta
    if palkinto / riski < MIN_RR:
        return None  # ei tilaa nousulle
    return {"sl": round(float(sl), 2), "tp": round(float(tp), 2), "rr": round(float(palkinto / riski), 2),
            "sl_pct": float(riski / hinta * 100), "atr": round(float(atr), 2)}


def botti_1_analysoi(ticker_symbol):
    """Palauttaa kandidaattisanakirjan tai None."""
    t = yf.Ticker(ticker_symbol)
    df = t.history(period="1y")
    if len(df) < 200:
        return None

    df["SMA200"] = df["Close"].rolling(200).mean()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()   # Wilder RSI
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + gain / loss)

    hinta = df["Close"].iloc[-1]
    muutos = (hinta / df["Close"].iloc[-2] - 1) * 100
    rsi = df["RSI"].iloc[-1]
    sma200 = df["SMA200"].iloc[-1]

    if not ((muutos <= -2.0 or rsi <= 40) and hinta > sma200 * 0.97):
        return None

    # 4 h -suodatin: ei osteta, jos päivän viimeiset tunnit vielä laskevat
    try:
        h = t.history(period="5d", interval="1h")
        if len(h) >= 4 and hinta < h["Close"].iloc[-4]:
            print(f"[{ticker_symbol}] hylätty: viimeiset 4 h vielä laskussa")
            return None
    except Exception:
        pass

    tasot = laske_tasot(df, hinta)
    if not tasot:
        print(f"[{ticker_symbol}] hylätty: R/R tai SL-etäisyys ei kelpaa")
        return None

    tulos = hae_tulostiedot(t)
    if tulos["paivia_tulokseen"] is not None and tulos["paivia_tulokseen"] <= TULOSRAJA_PAIVIA:
        print(f"[{ticker_symbol}] hylätty: tulos {tulos['paivia_tulokseen']} pv päässä")
        return None

    fundamentit = hae_fundamentit(t)
    on_indeksi = ticker_symbol.startswith("^")
    if not fundamentit and not on_indeksi:
        print(f"[{ticker_symbol}] hylätty: ei fundamenttidataa")
        return None

    uutiset = hae_uutiset_ja_keskustelut(t)
    max_vipu = sallittu_max_vipu(tasot["sl_pct"] / 100)
    atr_pct = tasot["atr"] / hinta * 100
    prompt = f"""
Olet kriittinen osakeanalyytikko. Arvioi, onko {ticker_symbol}:n laskulle fundamentaalisesti
perusteltu syy (huonontuva kasvu, marginaalit, velka, tulosmiss) vai onko kyseessä terveellinen dippi.

SÄÄNNÖT: Käytä VAIN alla annettua dataa. Älä keksi lukuja. Jos tieto puuttuu, sano "ei dataa".
Älä mainitse todennäköisyyksiä. Viittaa perusteluissa konkreettisiin lukuihin.

TEKNINEN: hinta {hinta:.2f}, päivämuutos {muutos:.2f} %, RSI {rsi:.1f}, SMA200 {sma200:.2f}
RISKIPROFIILI: ATR {atr_pct:.1f} % hinnasta, SL {tasot['sl_pct']:.1f} % päässä, R/R 1:{tasot['rr']}, beta {fundamentit.get('beta', 'ei dataa')}

VIVUN VALINTA: Valitse Mini Long -vipu kokonaislukuna 1-{max_vipu} (koodi on jo rajannut sallitun maksimin).
Koodi mitoittaa panoksen niin, että tappio SL:ssä on aina sama ({RISKI_PROSENTTI * 100:.0f} % salkusta) vivusta riippumatta.
Vipu ei siis kasvata SL-tappiota, mutta korkea vipu lähentää knock-out-rajaa ja kasvattaa gap-riskiä
(yön yli -avaus voi ohittaa SL:n). Tavoite: paras tuotto minimaalisella riskillä.
Korkea vipu (7-{max_vipu}) vain, jos SL on lähellä, volatiliteetti matala, fundamentit vahvat (pisteet 8+) eikä
tulosjulkistusta tai muuta tapahtumariskiä ole lähellä. Epävarmassa tai volatiilissa tilanteessa valitse matala vipu (1-4).
FUNDAMENTIT (JSON): {json.dumps(fundamentit, default=str, ensure_ascii=False)}
TULOKSET: {json.dumps(tulos, ensure_ascii=False)}
UUTISET: {uutiset}

Vastaa vain JSON:
{{
  "paatos": "HYVÄKSYTTY" tai "HYLÄTTY",
  "pisteet": kokonaisluku 1-10 (fundamentaalinen laatu + dipin terveellisyys),
  "vipu": kokonaisluku 1-{max_vipu},
  "vipu_perustelu": "1 lause: miksi tämä vipu (volatiliteetti, SL-etäisyys, tapahtumariski)",
  "fundamentit": "2-3 lausetta: kasvu, kannattavuus, arvostus, velka - luvuilla",
  "dipin_syy": "mikä todennäköisesti selittää laskun",
  "riskit": "tärkeimmät 1-2 riskiä",
  "katalyytti": "mikä voisi ajaa nousun, tai 'ei selvää'"
}}"""
    try:
        a = parse_json(kutsu_llm(prompt))
    except Exception as e:
        print(f"[{ticker_symbol}] LLM-virhe: {e}")
        return None
    # Kirjataan myös hylätyt: samaa osaketta ei kysytä uudelleen saman viikon aikana (säästää tokeneita)
    tallenna_historia(ticker_symbol)

    try:
        pisteet = int(a.get("pisteet", 0)) if a else 0
    except (TypeError, ValueError):
        pisteet = 0
    if not a or a.get("paatos") != "HYVÄKSYTTY" or pisteet < MIN_PISTEET:
        return None
    a["pisteet"] = pisteet
    try:
        vipu = int(a.get("vipu", VIPU_OLETUS))
    except (TypeError, ValueError):
        vipu = VIPU_OLETUS
    vipu = max(MIN_VIPU, min(vipu, max_vipu))

    return {"ticker": ticker_symbol, "hinta": float(hinta), "rsi": float(rsi), "muutos": float(muutos),
            "sma200": float(sma200), "tasot": tasot, "analyysi": a,
            "vipu": vipu, "vipu_perustelu": a.get("vipu_perustelu", ""),
            "baripvm": str(df.index[-1].date()),
            "sektori": fundamentit.get("sektori", "indeksi" if on_indeksi else "tuntematon"),
            "tulos": tulos}


# --- BOTTI 2: SALKKU- JA TREIDILOGIIKKA ---
def lataa_salkku():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            salkku = json.load(f)
    else:
        salkku = {"kassa": 3000.00, "alkupääoma": 3000.00, "positiot": []}
    salkku.setdefault("toimeksiannot", [])
    return salkku


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
            except Exception:
                hinta_nyt = pos["ostohinta"]
            vipu = pos.get("vipu", 1)
            hinnan_muutos_pct = (hinta_nyt - pos["ostohinta"]) / pos["ostohinta"]
            jella_oleva_osuus = 0.5 if pos.get("osavoitto_kotiutettu") else 1.0
            vivutettu_arvo = (pos["panos"] * jella_oleva_osuus) * (1 + (hinnan_muutos_pct * vipu))
            avoimet_arvo += max(0, vivutettu_arvo)
    return salkku["kassa"] + avoimet_arvo


def luo_odottava_toimeksianto(k):
    """Yöajo luo vain toimeksiannon. Sisääntulo tapahtuu seuraavan kaupankäyntipäivän AVAUSHINNALLA."""
    salkku = lataa_salkku()
    odottavat = salkku["toimeksiannot"]
    avoimet = [p for p in salkku["positiot"] if p["tila"] == "AVOIN"]
    kaikki = avoimet + odottavat

    if len(kaikki) >= MAX_POSITIOITA:
        return False
    if any(p["ticker"] == k["ticker"] for p in kaikki):
        return False
    if sum(1 for p in kaikki if p.get("sektori") == k["sektori"]) >= MAX_PER_SEKTORI:
        print(f"[{k['ticker']}] ohitettu: sektoriraja ({k['sektori']})")
        return False

    a, t = k["analyysi"], k["tasot"]
    odottavat.append({
        "ticker": k["ticker"], "sektori": k["sektori"], "pisteet": a["pisteet"],
        "signaali_pvm": k["baripvm"], "signaalihinta": round(k["hinta"], 2),
        "sl": t["sl"], "tp": t["tp"], "rr_signaali": t["rr"],
        "vipu": k["vipu"], "vipu_perustelu": k["vipu_perustelu"],
        "rsi": round(k["rsi"], 1), "muutos": round(k["muutos"], 2), "sma200": round(k["sma200"], 2),
        "fundamentit": a.get("fundamentit", ""), "dipin_syy": a.get("dipin_syy", ""),
        "riskit": a.get("riskit", ""), "katalyytti": a.get("katalyytti", ""),
    })
    tallenna_salkku(salkku)
    send_telegram_message(
        f"📝 <b>TOIMEKSIANTO: {k['ticker']}</b> (täytetään seuraavan päivän avauksessa)\n"
        f"Signaalihinta {k['hinta']:.2f} | SL {t['sl']} | TP {t['tp']} | R/R 1:{t['rr']} | {a['pisteet']}/10 | vipu x{k['vipu']}\n"
        f"{a.get('fundamentit', '')}\nRiskit: {a.get('riskit', '')}"
    )
    return True


def toteuta_toimeksiannot():
    """Täyttää toimeksiannot signaalin JÄLKEISEN ensimmäisen kynttilän avaushinnalla,
    tarkistaa R/R:n uudelleen ja laskee panoksen uudelleen (riski pysyy RISKI_PROSENTTI:ssa)."""
    salkku = lataa_salkku()
    odottavat = salkku["toimeksiannot"]
    if not odottavat:
        return
    jaljella = []

    for o in odottavat:
        try:
            hist = yf.Ticker(o["ticker"]).history(period="15d")
            hist = hist[hist.index.strftime("%Y-%m-%d") > o["signaali_pvm"]]
        except Exception:
            jaljella.append(o)
            continue

        if hist.empty:   # esim. viikonloppu tai data puuttuu: odotetaan
            ika = (get_finland_time().date() - datetime.strptime(o["signaali_pvm"], "%Y-%m-%d").date()).days
            if ika > TOIMEKSIANNON_VANHENEMINEN_PV:
                send_telegram_message(f"⌛ <b>Toimeksianto vanheni: {o['ticker']}</b>")
            else:
                jaljella.append(o)
            continue

        bar = hist.iloc[0]
        entry = float(bar["Open"])
        sl, tp = o["sl"], o["tp"]
        riski, palkinto = entry - sl, tp - entry
        avoimet = [p for p in salkku["positiot"] if p["tila"] == "AVOIN"]
        saldo = laske_kokonaissaldo(salkku)

        syy = None
        if riski <= 0:
            syy = f"avaus {entry:.2f} SL:n ({sl}) alapuolella"
        elif palkinto <= 0:
            syy = f"avaus {entry:.2f} jo TP:n ({tp}) yläpuolella"
        elif palkinto / riski < MIN_RR_TOTEUTUS:
            syy = f"R/R avauksessa vain 1:{palkinto / riski:.2f}"
        elif riski / entry > MAX_SL_ETAISYYS:
            syy = "SL liian kaukana avaushinnasta"
        elif len(avoimet) >= MAX_POSITIOITA:
            syy = "positioraja täynnä"

        panos = 0.0
        vipu = VIPU_OLETUS
        if not syy:
            sl_osuus = riski / entry
            # Tekoälyn ehdottama vipu rajataan toteutushinnalla: SL pysyy selvästi knock-outin sisäpuolella
            vipu = max(MIN_VIPU, min(int(o.get("vipu", VIPU_OLETUS)), sallittu_max_vipu(sl_osuus)))
            riski_eur = saldo * RISKI_PROSENTTI
            # Jos panos jäisi liian pieneksi, pienennetään vipua (suurempi panos samalla riskillä)
            while vipu > MIN_VIPU and riski_eur / (vipu * sl_osuus) < MIN_PANOS:
                vipu -= 1
            panos = riski_eur / (vipu * sl_osuus)
            panos = round(min(panos, salkku["kassa"] - saldo * KASSA_MIN_PROSENTTI, saldo * MAX_PANOS_OSUUS), 2)
            if panos < MIN_PANOS:
                syy = f"panos < {MIN_PANOS:.0f} € (kassareservi)"

        if syy:
            print(f"[{o['ticker']}] toimeksianto peruttu: {syy}")
            send_telegram_message(f"❌ <b>Toimeksianto peruttu: {o['ticker']}</b>\n{syy}")
            continue

        gap = (entry / o["signaalihinta"] - 1) * 100
        rr = palkinto / riski
        perustelu = (
            f"<b>Fundamentit ({o['pisteet']}/10):</b> {o['fundamentit']}<br>"
            f"<b>Dipin syy:</b> {o['dipin_syy']}<br>"
            f"<b>Riskit:</b> {o['riskit']}<br>"
            f"<b>Katalyytti:</b> {o['katalyytti']}<br>"
            f"<b>Vipu x{vipu}:</b> {o.get('vipu_perustelu', '')} "
            f"(knock-out ≈ {100 / vipu:.0f} % alempana, SL {riski / entry * 100:.1f} %)<br>"
            f"<b>Tekninen:</b> RSI {o['rsi']}, päivä {o['muutos']} %, SMA200 {o['sma200']}. "
            f"SL {sl} (1,5×ATR / swing low), TP {tp} (vastustaso).<br>"
            f"<b>Toteutus:</b> signaali {o['signaali_pvm']} sulku {o['signaalihinta']}, "
            f"täyttö avaushinnalla {entry:.2f} (gap {gap:+.2f} %), R/R toteutuksessa 1:{rr:.2f}. "
            f"Riski {saldo * RISKI_PROSENTTI:.0f} € ({RISKI_PROSENTTI * 100:.0f} % salkusta)."
        )
        salkku["kassa"] -= panos
        salkku["positiot"].append({
            "id": len(salkku["positiot"]) + 1, "ticker": o["ticker"], "tila": "AVOIN",
            "vipu": vipu, "ostohinta": round(entry, 2), "panos": panos,
            "stop_loss": sl, "tavoite": tp,
            "pvm": str(hist.index[0].date()),            # todellinen kaupankäyntipäivä
            # täyttöpäivän kynttilä (avauksesta eteenpäin) kuuluu SL/TP-tarkistukseen:
            "baripvm": o["signaali_pvm"], "viimeksi_tarkistettu": o["signaali_pvm"],
            "sektori": o["sektori"], "pisteet": o["pisteet"],
            "osavoitto_kotiutettu": False, "aloitus_perustelu": perustelu,
            "toteutunut_tulos_eur": 0.0, "toteutunut_tulos_pct": 0.0, "sulkusyy": "",
        })
        send_telegram_message(
            f"🤖 <b>UUSI MINI LONG: {o['ticker']} x{vipu}</b>\n"
            f"Täyttö {entry:.2f} (gap {gap:+.2f} %) | Panos {panos:.2f} € | R/R 1:{rr:.2f}\n"
            f"SL {sl} | TP {tp}"
        )

    salkku["toimeksiannot"] = jaljella
    tallenna_salkku(salkku)


def hae_baarit_avauksen_jalkeen(pos):
    """Vain kynttilät, joita ei ole vielä tarkistettu. Estää avauspäivän vanhan Low:n aiheuttaman
    väärän stopin sekä osavoiton jälkeen siirretyn SL:n tarkistamisen vanhoja kynttilöitä vastaan."""
    raja = pos.get("viimeksi_tarkistettu", pos.get("baripvm", pos["pvm"]))
    hist = yf.Ticker(pos["ticker"]).history(period="15d")
    return hist[hist.index.strftime("%Y-%m-%d") > raja]


def tarvitsee_ai_tarkistuksen(pos, current_price, ticker_obj):
    """Säästää tokeneita: LLM kysytään sulkupäätökseen vain, jos tappio on syvä tai tulos on lähellä."""
    vivutettu = (current_price / pos["ostohinta"] - 1) * pos.get("vipu", 1) * 100
    if vivutettu <= -8:
        return True
    paivia = hae_tulostiedot(ticker_obj)["paivia_tulokseen"]
    return paivia is not None and paivia <= 2


def sulje_positio(pos, salkku, hinta, syy):
    vipu = pos.get("vipu", 1)
    vivutettu_tulos_pct = ((hinta - pos["ostohinta"]) / pos["ostohinta"]) * vipu
    jella_oleva_osuus = 0.5 if pos.get("osavoitto_kotiutettu") else 1.0
    panos_osa = pos["panos"] * jella_oleva_osuus
    loppuarvo = max(0, panos_osa * (1 + vivutettu_tulos_pct))

    pos["tila"] = "SULJETTU"
    pos["toteutunut_tulos_eur"] = pos.get("toteutunut_tulos_eur", 0.0) + (loppuarvo - panos_osa)
    pos["toteutunut_tulos_pct"] = vivutettu_tulos_pct * 100
    pos["sulkusyy"] = syy
    salkku["kassa"] += loppuarvo


def kotiuta_osavoitto(pos, salkku, hinta):
    vipu = pos.get("vipu", 1)
    vivutettu_tulos_pct = ((hinta - pos["ostohinta"]) / pos["ostohinta"]) * vipu
    myyty_osuus_panoksesta = pos["panos"] / 2
    myyty_arvo = myyty_osuus_panoksesta * (1 + vivutettu_tulos_pct)

    pos["osavoitto_kotiutettu"] = True
    pos["toteutunut_tulos_eur"] = pos.get("toteutunut_tulos_eur", 0.0) + (myyty_arvo - myyty_osuus_panoksesta)
    pos["stop_loss"] = pos["ostohinta"]
    salkku["kassa"] += myyty_arvo


def paivita_avoimet_treidit():
    salkku = lataa_salkku()
    muutos_tehty = False

    for pos in salkku["positiot"]:
        if pos["tila"] != "AVOIN":
            continue
        try:
            hist = hae_baarit_avauksen_jalkeen(pos)
        except Exception as e:
            print(f"[{pos['ticker']}] kurssihaku epäonnistui: {e}")
            continue
        if hist.empty:
            continue

        vipu = pos.get("vipu", 1)

        # Käydään uudet kynttilät läpi kronologisesti
        for _, bar in hist.iterrows():
            # 1. Stop-Loss (gap alas toteutuu avaushinnalla, jos se on SL:ää huonompi)
            if bar["Low"] <= pos["stop_loss"]:
                toteutus = min(pos["stop_loss"], float(bar["Open"]))
                sulje_positio(pos, salkku, toteutus,
                              f"Stop-Loss laukesi (päivän alin {bar['Low']:.2f} alitti tason {pos['stop_loss']:.2f}, toteutus {toteutus:.2f})")
                send_telegram_message(
                    f"🛑 <b>BOTTI 2: STOP-LOSS LAUKESI ({pos['ticker']} Vipu x{vipu})</b>\n"
                    f"Päivän alin ({bar['Low']:.2f}) osui stop-lossiin ({pos['stop_loss']:.2f}). Toteutus {toteutus:.2f}. Positio suljettu."
                )
                break

            # 2. Take Profit (osavoitto): ylös gappaava avaus toteutuu avaushinnalla
            if not pos.get("osavoitto_kotiutettu") and bar["High"] >= pos["tavoite"]:
                toteutus = max(pos["tavoite"], float(bar["Open"]))
                kotiuta_osavoitto(pos, salkku, toteutus)
                send_telegram_message(
                    f"🎯 <b>BOTTI 2: OSAVOITTO KOTIUTETTU ({pos['ticker']} Vipu x{vipu})</b>\n"
                    f"Päivän ylin ({bar['High']:.2f}) saavutti tavoitteen ({pos['tavoite']:.2f}). "
                    f"Myyty 50 % hintaan {toteutus:.2f}, stop-loss nostettu sisäänhintaan!"
                )
                # SL (nyt sisäänhinta) tarkistetaan seuraavista kynttilöistä

        pos["viimeksi_tarkistettu"] = str(hist.index[-1].date())
        muutos_tehty = True

        if pos["tila"] != "AVOIN":
            continue

        # 3. TEKOÄLYN SULKUPÄÄTÖS (vain jos tappio syvä tai tulos lähellä)
        try:
            current_price = float(hist["Close"].iloc[-1])
            ticker = yf.Ticker(pos["ticker"])
            if not tarvitsee_ai_tarkistuksen(pos, current_price, ticker):
                continue
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
            ai_decision = parse_json(kutsu_llm(prompt))
            if ai_decision and ai_decision.get("sulje_positio"):
                sulje_positio(pos, salkku, current_price,
                              f"Tekoälyn ennenaikainen myynti: {ai_decision.get('perustelu')}")
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
        <title>Treidausbotti - Autonominen Viputreidaussalkku</title>
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
                    <li><b>Tekninen nousutrendi:</b> Osakkeen nykyhinnan tulee olla pitkän aikavälin nousutrendissä (<b>hinta &gt; 97% SMA200</b>).</li>
                    <li><b>Dippi / Ylilyönti:</b> Haetaan tilapäistä korjausliikettä (päivälasku <b>≤ -2.0%</b> tai 14p <b>RSI ≤ 40</b>), jonka jälkeen päivän viimeiset tunnit eivät enää laske.</li>
                    <li><b>Fundamenttianalyysi:</b> Groq-tekoäly saa kasvu-, kannattavuus-, velka- ja arvostusluvut, tulosyllätykset sekä uutiset. Kohde pisteytetään 1-10 ja vähintään <b>{MIN_PISTEET}/10</b> vaaditaan. Treidiä ei avata, jos tulosjulkistus on alle {TULOSRAJA_PAIVIA} päivän päässä.</li>
                    <li><b>Risk/Reward vähintään 1:{MIN_RR:.0f}:</b> Stop-Loss (1,5 × ATR tai viimeisin pohja) ja Take Profit (lähin vastustaso) lasketaan koodissa, ei tekoälyllä. Jos tilaa nousulle ei ole, treidiä ei avata.</li>
                    <li><b>Riskinhallinta:</b> Tekoäly valitsee vivun (x{MIN_VIPU}–x{MAX_VIPU}), jonka koodi rajaa niin, että SL on enintään {KO_PUFFER * 100:.0f}% matkasta knock-out-tasolle. Panos mitoitetaan niin, että tappio SL:ssä on enintään {RISKI_PROSENTTI * 100:.0f}% salkusta vivusta riippumatta. Lisäksi enintään {MAX_POSITIOITA} positiota ({MAX_PER_SEKTORI} per sektori) ja vähintään {KASSA_MIN_PROSENTTI * 100:.0f}% kassaa.</li>
                    <li><b>Toteutus:</b> Signaali syntyy illalla päivän sulkuhinnoista, ja kauppa täytetään seuraavan päivän <b>avaushinnalla</b>. R/R tarkistetaan uudelleen toteutushinnalla.</li>
                    <li><b>Kotiutus:</b> Tavoitteessa myydään 50% ja Stop-Loss nostetaan sisäänhintaan (Break-Even).</li>
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
            except Exception:
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

            <h2>Odottavat toimeksiannot (täytetään seuraavan päivän avauksessa)</h2>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Kohde</th>
                            <th>Signaalipvm</th>
                            <th>Signaalihinta</th>
                            <th>Stop-Loss / Tavoite</th>
                            <th>Pisteet</th>
                            <th>Perustelu</th>
                        </tr>
                    </thead>
                    <tbody>
    """

    if salkku.get("toimeksiannot"):
        for o in salkku["toimeksiannot"]:
            html_content += f"""
                <tr>
                    <td data-label="Kohde"><b>{o['ticker']}</b> <span class="badge">Ehdotettu vipu x{o.get('vipu', VIPU_OLETUS)}</span></td>
                    <td data-label="Signaalipvm" class="nowrap">{o['signaali_pvm']}</td>
                    <td data-label="Signaalihinta" class="nowrap">{o['signaalihinta']:.2f} €</td>
                    <td data-label="SL / TP" class="nowrap"><span class="neg">SL: {o['sl']:.2f} €</span><br><span class="pos">TP: {o['tp']:.2f} €</span></td>
                    <td data-label="Pisteet" class="nowrap">{o['pisteet']}/10</td>
                    <td data-label="Perustelu"><div class="reason-box"><b>Fundamentit:</b> {o['fundamentit']}<br><b>Riskit:</b> {o['riskit']}</div></td>
                </tr>
    """
    else:
        html_content += "<tr><td colspan='6' style='text-align: center; color: #777;'>Ei odottavia toimeksiantoja.</td></tr>"

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
    print("--- Toimeksiantojen täyttö (edellisen illan signaalit) ---")
    toteuta_toimeksiannot()

    print("--- Botti 1: Dippi- ja fundamenttivahti käynnissä ---")
    if not uusi_kaupankayntipaiva():
        print("Ei uutta kaupankäyntipäivää: ei etsitä uusia positioita.")
    else:
        lahetetyt_viikolla = lue_historia()
        week = get_finland_time().strftime("%Y-W%V")
        kandidaatit = []
        for ticker in TICKERS:
            if f"{ticker}_{week}" in lahetetyt_viikolla:
                continue
            try:
                k = botti_1_analysoi(ticker)
                if k:
                    print(f"[{ticker}] Botti 1 hyväksyi kohteen!")
                    kandidaatit.append(k)
            except Exception as e:
                print(f"Virhe osakkeen {ticker} käsittelyssä: {e}")

        # Paras ensin: pisteet, sitten R/R
        kandidaatit.sort(key=lambda k: (k["analyysi"]["pisteet"], k["tasot"]["rr"]), reverse=True)
        for k in kandidaatit:
            luo_odottava_toimeksianto(k)

    print("\n--- Botti 2: Päivitetään avoimet treidit & HTML-sivu ---")
    paivita_avoimet_treidit()
    paivita_salkku_html()


if __name__ == "__main__":
    main()
    main()
