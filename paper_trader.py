import json
import os
from datetime import datetime
from groq import Groq
import requests
import yfinance as yf

SIGNALS_FILE = "signals.json"
PORTFOLIO_FILE = "portfolio.json"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))


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


def send_telegram(message):
  if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print(f"\n[TELEGRAM SIMULAATIO]:\n{message}\n")
    return
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f"Virhe Telegram-viestissä: {e}")


def get_current_price(ticker):
  try:
    stock = yf.Ticker(ticker)
    data = stock.history(period="1d")
    if not data.empty:
      return float(data["Close"].iloc[-1])
  except Exception as e:
    print(f"Virhe haettaessa hintaa {ticker}: {e}")
  return None


def run_paper_trader():
  signals = load_json(SIGNALS_FILE, [])
  portfolio = load_json(
      PORTFOLIO_FILE,
      {"cash": 3000.0, "total_starting": 3000.0, "positions": []},
  )

  positions = portfolio["positions"]
  positions_to_keep = []
  reports = []

  # 1. Tarkistetaan avoimet positiot (Stop-Loss ja vastustason 50% kotiutus)
  for pos in positions:
    ticker = pos["ticker"]
    cp = get_current_price(ticker)
    if not cp:
      positions_to_keep.append(pos)
      continue

    buy_price = pos["buy_price"]
    leverage = pos["leverage"]
    p_type = pos["type"]
    sl_pct = pos["stop_loss_pct"]
    target_price = pos.get("target_price", buy_price * 1.08)

    price_chg_pct = ((cp - buy_price) / buy_price) * 100
    if "SHORT" in p_type:
      price_chg_pct = -price_chg_pct

    leveraged_pct = price_chg_pct * leverage

    # Stop-Loss
    if sl_pct > 0 and price_chg_pct <= -abs(sl_pct):
      realized_loss = pos["invested"] * ((abs(sl_pct) / 100) * leverage)
      actual_return = max(pos["invested"] - realized_loss, 0.0)
      portfolio["cash"] += actual_return
      reports.append(
          f"🛑 <b>STOP-LOSS LAUKESI:</b> {ticker} ({p_type} {leverage}x)\n"
          f"Tappio rajattu: -{min(realized_loss, pos['invested']):.2f} €"
      )
      continue

    # Scaling out (50% voittojen kotiutus vastustasolla)
    if not pos.get("half_take_profit_done", False):
      reached_target = (
          cp >= target_price
          if "LONG" in p_type
          else cp <= target_price
      )
      if reached_target:
        half_inv = pos["invested"] / 2
        sale_val = half_inv * (1 + (leveraged_pct / 100))
        portfolio["cash"] += sale_val

        pos["invested"] = half_inv
        pos["half_take_profit_done"] = True
        pos["stop_loss_pct"] = 0.0  # Break-even

        reports.append(
            f"🎯 <b>TAVOITETASO SAAVUTETTU ({cp:.2f} €):</b> Myyty 50 %"
            f" kohteesta <b>{ticker}</b>.\n"
            f"Kotiutettu: {sale_val:.2f} € (+{leveraged_pct:.1f}%)\n"
            f"Loput 50 % rullaavat ilman riskiä!"
        )

    positions_to_keep.append(pos)

  portfolio["positions"] = positions_to_keep

  # Salkun arvo
  total_val = portfolio["cash"]
  for pos in portfolio["positions"]:
    cp = get_current_price(pos["ticker"])
    if cp:
      chg = ((cp - pos["buy_price"]) / pos["buy_price"]) * 100
      if "SHORT" in pos["type"]:
        chg = -chg
      total_val += max(
          pos["invested"] * (1 + ((chg * pos["leverage"]) / 100)), 0.0
      )
    else:
      total_val += pos["invested"]

  max_allowed_risk_eur = total_val * 0.10  # Max 10% eli ~300€ riski

  # 2. Käsitellään uudet signaalit
  unprocessed = [s for s in signals if not s.get("processed", False)]
  for sig in unprocessed:
    ticker = sig.get("ticker")
    note = sig.get("technical_note", "")
    rsi = sig.get("rsi", "N/A")
    catalyst = sig.get("catalyyti", "")
    current_price = get_current_price(ticker)

    if not current_price:
      sig["processed"] = True
      continue

    prompt = f"""
        Olet autonominen kvantitatiivinen sijoitusbotti. Hallinnoit 3000 € leikkisalkkua minifutuureilla.
        TAVOITTEESI: Saavuttaa vähintään 2/3 (66.7 %) onnistumisprosentti. Ole erittäin valikoiva!
        Riskisäännöt: Maksimitappio per treidi enintään {max_allowed_risk_eur:.2f} €.

        Signaali:
        - Tikkeri: {ticker}, Hinta: {current_price} €
        - RSI: {rsi}, Huomio: {note}, Katalyytti: {catalyst}

        Palauta AINOASTAAN JSON:
        {{
            "approved": true/false,
            "action": "BUY" tai "HOLD",
            "instrument_type": "MINI LONG" tai "MINI SHORT",
            "leverage": 3.0,
            "amount_eur": 700.0,
            "stop_loss_underlying_pct": 6.0,
            "target_price_eur": {current_price * 1.08},
            "reason": "Perustelu suomeksi."
        }}
        """

    try:
      response = client.chat.completions.create(
          model="llama-3.3-70b-versatile",
          messages=[{"role": "user", "content": prompt}],
          response_format={"type": "json_object"},
      )
      ai_res = json.loads(response.choices[0].message.content)
    except Exception as e:
      print(f"AI-virhe: {e}")
      sig["processed"] = True
      continue

    if ai_res.get("approved") and ai_res.get("action") == "BUY":
      amount = float(ai_res.get("amount_eur", 600.0))
      leverage = float(ai_res.get("leverage", 3.0))
      inst_type = ai_res.get("instrument_type", "MINI LONG").upper()
      sl_pct = float(ai_res.get("stop_loss_underlying_pct", 7.0))
      target_p = float(ai_res.get("target_price_eur", current_price * 1.08))

      calc_loss = amount * ((sl_pct / 100) * leverage)
      if calc_loss > max_allowed_risk_eur:
        reports.append(
            f"⚠️ Hylätty ({ticker}): Riski ({calc_loss:.2f} €) ylittää"
            f" {max_allowed_risk_eur:.2f} € rajan."
        )
      elif portfolio["cash"] >= amount:
        portfolio["cash"] -= amount
        portfolio["positions"].append({
            "ticker": ticker,
            "type": inst_type,
            "leverage": leverage,
            "buy_price": current_price,
            "invested": amount,
            "stop_loss_pct": sl_pct,
            "target_price": target_p,
            "half_take_profit_done": False,
            "buy_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        reports.append(
            f"🟢 <b>UUSI TREIDI AVATTU</b>\n"
            f"- Kohde: <b>{ticker}</b> ({inst_type} {leverage}x)\n"
            f"- Sijoitus: {amount:.2f} € | Tavoite: {target_p:.2f} €\n"
            f"- Maksimiriski (SL {sl_pct}%): ~{calc_loss:.2f} €"
        )
      else:
        reports.append(f"⚠️ Käteinen ei riitä kohteelle {ticker}.")
    else:
      reports.append(f"❌ Hylätty: {ai_res.get('reason', '')}")

    sig["processed"] = True

  if reports:
    final_val = portfolio["cash"]
    for p in portfolio["positions"]:
      cp = get_current_price(p["ticker"])
      if cp:
        c_pct = ((cp - p["buy_price"]) / p["buy_price"]) * 100
        if "SHORT" in p["type"]:
          c_pct = -c_pct
        final_val += max(p["invested"] * (1 + ((c_pct * p["leverage"]) / 100)), 0.0)
      else:
        final_val += p["invested"]

    full_msg = (
        f"🤖 <b>AI-Sijoitusbotin Raportti</b>\n\n"
        + "\n\n".join(reports)
        + f"\n\n📊 <b>Salkku yhteensä:</b> {final_val:.2f} €"
        f" ({((final_val - 3000) / 3000) * 100:+.2f} %)"
    )
    send_telegram(full_msg)

  save_json(SIGNALS_FILE, signals)
  save_json(PORTFOLIO_FILE, portfolio)


if __name__ == "__main__":
  run_paper_trader()