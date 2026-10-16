import os
import requests
import yfinance as yf
import pandas as pd
from groq import Groq

# 1. API-avaimet
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# 2. Seurantalista: Indeksit & Keskeiset osakkeet, joille löytyy Nordnet Mini Long / Futuuri -tuotteita
SEURANTALISTA = [
    # Indeksit (Nordnet Mini Long DAX, Nasdaq, S&P 500, OMXH25)
    "^GDAXI",   # DAX 40 (Saksa)
    "^NDX",     # Nasdaq 100 (USA Tekno)
    "^GSPC",    # S&P 500 (USA)
    "^OMXH25",  # OMX Helsinki 25

    # Suomi (Nordnet Mini Long -suosikit)
    "NOKIA.HE", "NESTE.HE", "UPM.HE", "FORTUM.HE", "KONE.HE", 
    "SAMPO.HE", "NDA-FI.HE", "WRT1V.HE", "VALMT.HE", "METSA.HE",

    # USA (Nordnet Mini Long -suosikit)
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "AMD", "META"
]

# 3. Ostostrategian kriteerit
PUDOTUS_KYNNYS = -2.0  # Päivälasku vähintään -2.0 %
RSI_YLIMYYTY = 35.0    # RSI alle 35 tulkitaan ylimyydyksi/houkuttelevaksi

def laski_rsi(series, period=14):
    """Laskee RSI-indikaattorin (Relative Strength Index)."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def laheta_telegram(viesti):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": viesti, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def analysoi_ja_varoita(symboli, muutos, hinta, rsi_arvo, sma200_arvo, ehto_syy):
    client = Groq(api_key=GROQ_API_KEY)
    
    prompt = (
        f"Sijoituskohde/Indeksi {symboli} täyttää ostostrategian kriteerit ({ehto_syy})! "
        f"Tunnusluvut:\n"
        f"- Päivämuutos: {muutos:.2f}%\n"
        f"- Nykyhinta: {hinta:.2f}\n"
        f"- RSI (14): {rsi_arvo:.1f}\n"
        f"- 200 päivän keskiarvo (SMA200): {sma200_arvo:.2f}\n\n"
        f"Huomioi, että kohteelle löytyy Nordnetistä viputuotteita (Mini Long / Futuurit).\n"
        f"Kirjoita todella tiivis, innostava ja selkeä suomenkielinen Telegram-hälytys sijoittajalle. "
        f"Korosta mahdollista ostopaikkaa tai 'dipin ostamista' vivulla/futuureilla."
    )
    
    completion = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    
    vastaus_teksti = completion.choices[0].message.content
    viesti = f"🚨 **OSTOSTRATEGIA HÄLYTYS: {symboli}** 🚨\n\n{vastaus_teksti}"
    laheta_telegram(viesti)

def main():
    print(f"Aloitetaan screeneri: käydään läpi {len(SEURANTALISTA)} kohdetta...")
    
    for symboli in SEURANTALISTA:
        try:
            osake = yf.Ticker(symboli)
            # Haetaan 1 vuosi historiaa, jotta 200 päivän keskiarvo (SMA200) voidaan laskea
            hist = osake.history(period="1y")
            
            if len(hist) < 200:
                # Jos dataa liian vähän SMA200-laskentaan, otetaan pienempi historia
                hist = osake.history(period="1mo")
                if len(hist) < 2:
                    continue

            # Lasketaan tekniset indikaattorit
            hist['RSI'] = laski_rsi(hist['Close'], period=14)
            hist['SMA200'] = hist['Close'].rolling(window=200).mean()

            edellinen_sulkeminen = hist['Close'].iloc[-2]
            viimeisin_hinta = hist['Close'].iloc[-1]
            muutos_prosentti = ((viimeisin_hinta - edellinen_sulkeminen) / edellinen_sulkeminen) * 100
            
            rsi_arvo = hist['RSI'].iloc[-1] if not pd.isna(hist['RSI'].iloc[-1]) else 50.0
            sma200_arvo = hist['SMA200'].iloc[-1] if not pd.isna(hist['SMA200'].iloc[-1]) else viimeisin_hinta

            print(f"Tarkistettu: {symboli:10s} | Hinta: {viimeisin_hinta:8.2f} | Muutos: {muutos_prosentti:6.2f}% | RSI: {rsi_arvo:5.1f} | SMA200: {sma200_arvo:8.2f}")

            # 🎯 OSTOSTRATEGIAN EHDOT:
            # Ehto 1: Voimakas päivälasku (<-2%) JA kohde on RSI:n mukaan ylimyyty (<35)
            # Ehto 2: Hinta tippunut 200 päivän keskiarvon alle tai lähelle sitä
            osuma = False
            syy = ""

            if muutos_prosentti <= PUDOTUS_KYNNYS and rsi_arvo <= RSI_YLIMYYTY:
                osuma = True
                syy = f"Päivälasku {muutos_prosentti:.2f}% & Ylimyyty RSI ({rsi_arvo:.1f})"
            elif muutos_prosentti <= PUDOTUS_KYNNYS and viimeisin_hinta < sma200_arvo:
                osuma = True
                syy = f"Päivälasku {muutos_prosentti:.2f}% & Hinta alle 200 päivän keskiarvon"

            if osuma:
                print(f"🎯 OSTOSTRATEGIA OSUMA: {symboli} ({syy})!")
                analysoi_ja_varoita(symboli, muutos_prosentti, viimeisin_hinta, rsi_arvo, sma200_arvo, syy)

        except Exception as e:
            print(f"Virhe käsiteltäessä kohdetta {symboli}: {e}")

    print("Kaikki kohteet tarkistettu.")

if __name__ == "__main__":
    main()
