#!/usr/bin/env python3
"""
Beat-Up Recovery Scanner — weekly scan script.
Runs every Sunday via GitHub Actions → writes public/tools/stock-picks/results.json.

Steps:
  1. Fetch candidate universe from Finviz (large-cap, high vol, RSI oversold, below 50% ATH)
  2. Pull weekly TradingView TA for each candidate
  3. Apply 6 hard filters
  4. Score survivors 0-10 (technical) + 0-3 (Adanos social sentiment)
  5. Classify recovery stage
  6. Write results.json
"""

import json
import os
import sys
import time
import datetime
import traceback
import urllib.request

# ── Dependencies ────────────────────────────────────────────────────────────
try:
    from finviz.screener import Screener
except ImportError:
    print("Installing finviz…")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "finviz", "-q"])
    from finviz.screener import Screener

try:
    from tradingview_ta import TA_Handler, Interval
except ImportError:
    print("Installing tradingview_ta…")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "tradingview_ta", "-q"])
    from tradingview_ta import TA_Handler, Interval

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "yfinance", "-q"])
    import yfinance as yf

import requests

# ── Config ─────────────────────────────────────────────────────────────────
ADANOS_KEY = os.environ.get("ADANOS_API_KEY", "sk_live_da15ca691fc131961faf2587b8e7f0f5")
ADANOS_HEADERS = {"X-API-Key": ADANOS_KEY, "User-Agent": "Mozilla/5.0"}
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "public", "tools", "stock-picks", "results.json")

# NYSE tickers that need exchange="NYSE" in tradingview_ta
NYSE_TICKERS = {
    "V","MA","NOW","CRM","SNOW","PLTR","ANET","MU","DELL","NET","DDOG",
    "HUBS","ZS","WDAY","LLY","UNH","OKLO","CEG","HOOD","GS","MS",
    "GE","IBM","JPM","BAC","WFC","C","AXP","DIS","KO","PG","JNJ",
    "UNP","CAT","MMM","XOM","CVX","BA","RTX","LMT","HON",
}

def get_exchange(ticker):
    return "NYSE" if ticker in NYSE_TICKERS else "NASDAQ"


# ── Step 1: Finviz universe ──────────────────────────────────────────────
def get_candidates():
    print("Step 1: Fetching Finviz universe…")
    filters = [
        "geo_usa",
        "cap_largeover",
        "sh_avgvol_o1000",
        "ta_rsi_os50",
        "ta_highlow52w_b50h",
    ]
    try:
        stock_list = Screener(filters=filters, table="Overview", order="-change")
        tickers = [s["Ticker"] for s in stock_list if s.get("Ticker")]
        print(f"  → {len(tickers)} candidates: {tickers[:10]}{'…' if len(tickers)>10 else ''}")
        return tickers
    except Exception as e:
        print(f"  ⚠️  Finviz failed: {e} — using fallback list")
        return [
            "TEAM","ZS","WDAY","TTD","INTU","MSTR","Z","FISV","SMCI","COIN",
            "ORCL","NOW","DKNG","CHTR","PODD","AXON","KTOS","NVDA","META",
            "AMZN","GOOGL","MSFT","AAPL","AMD","NFLX","SNOW","PLTR","SHOP",
        ]


# ── Step 2: TradingView weekly TA ────────────────────────────────────────
def get_tv_analysis(ticker):
    exchange = get_exchange(ticker)
    try:
        handler = TA_Handler(
            symbol=ticker,
            exchange=exchange,
            screener="america",
            interval=Interval.INTERVAL_1_WEEK,
        )
        analysis = handler.get_analysis()
        return analysis
    except Exception:
        # Try the other exchange on failure
        alt = "NYSE" if exchange == "NASDAQ" else "NASDAQ"
        try:
            handler = TA_Handler(symbol=ticker, exchange=alt, screener="america",
                                 interval=Interval.INTERVAL_1_WEEK)
            return handler.get_analysis()
        except Exception as e2:
            print(f"  TV failed {ticker}: {e2}")
            return None


def extract_indicators(analysis):
    """Pull the fields we need from tradingview_ta analysis object."""
    if not analysis:
        return None
    ind = analysis.indicators
    # 52-week high from TV or yfinance fallback
    high_52w = ind.get("high_52_week") or ind.get("High.52W") or ind.get("52WeekHigh") or 0
    price = ind.get("close") or ind.get("Recommend.All") and None or 0
    # Use the actual close price
    price = ind.get("close") or 0

    ema200 = ind.get("EMA200") or ind.get("ema_200") or 0
    ema20  = ind.get("EMA20")  or ind.get("ema_20")  or 0
    macd_h = ind.get("MACD.macd") or ind.get("macd") or 0   # histogram proxy
    adx    = ind.get("ADX") or ind.get("adx") or 0
    rsi    = ind.get("RSI") or ind.get("rsi") or 0
    stoch_k = ind.get("Stoch.K") or ind.get("stoch_k") or 50
    stoch_d = ind.get("Stoch.D") or ind.get("stoch_d") or 50

    candle_open  = ind.get("open") or price
    candle_close = ind.get("close") or price
    candle_high  = ind.get("high") or price
    candle_low   = ind.get("low") or price

    candle_range = candle_high - candle_low
    lower_wick   = min(candle_open, candle_close) - candle_low
    lower_wick_pct = (lower_wick / candle_range * 100) if candle_range > 0 else 0

    pct_below_ath = (high_52w - price) / high_52w * 100 if high_52w > 0 else 0

    # MACD signal: positive histogram = bullish
    macd_bullish = macd_h > 0

    # RSI direction: compare to prior week (approximate via Stoch trend)
    rsi_rising = stoch_k > stoch_d  # crude proxy

    return {
        "price": price,
        "high_52w": high_52w,
        "ema200": ema200,
        "ema20": ema20,
        "macd_histogram": macd_h,
        "macd_bullish": macd_bullish,
        "adx": adx,
        "rsi": rsi,
        "rsi_rising": rsi_rising,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "lower_wick_pct": lower_wick_pct,
        "pct_below_ath": pct_below_ath,
    }


# ── Step 3: 6 Hard Filters ───────────────────────────────────────────────
def apply_filters(ticker, ind):
    reasons = []
    pba = ind["pct_below_ath"]
    if not (15 <= pba <= 60):
        reasons.append(f"ATH drawdown {pba:.1f}% (need 15-60%)")
    if ind["price"] <= ind["ema200"]:
        reasons.append("Price below EMA200")
    if not ind["macd_bullish"]:
        reasons.append("MACD histogram ≤ 0")
    adx = ind["adx"]
    if not (adx < 32):
        reasons.append(f"ADX {adx:.0f} (need <32)")
    rsi = ind["rsi"]
    if not (28 <= rsi <= 54):
        reasons.append(f"RSI {rsi:.0f} (need 28-54)")
    if ind["lower_wick_pct"] < 25:
        reasons.append(f"Lower wick {ind['lower_wick_pct']:.0f}% (need ≥25%)")
    passed = len(reasons) == 0
    return passed, reasons


# ── Step 4: Technical Score 0-10 ─────────────────────────────────────────
def tech_score(ind):
    score = 0
    signals = []
    if ind["stoch_k"] > ind["stoch_d"]:
        score += 2; signals.append("Stoch K>D")
    if ind["rsi_rising"]:
        score += 1; signals.append("RSI Rising")
    if ind["price"] > ind["ema20"]:
        score += 2; signals.append("Above EMA20")
    if ind["adx"] < 20:
        score += 1; signals.append("ADX<20 (quiet)")
    if ind["lower_wick_pct"] >= 50:
        score += 1; signals.append("Long Wick")
    if ind["macd_bullish"]:
        score += 2; signals.append("MACD Bullish")
    if ind["adx"] < 25:
        score += 1; signals.append("ADX Weak")
    return min(score, 10), signals


# ── Step 5: Social Sentiment via Adanos ──────────────────────────────────
def get_adanos(platform, ticker):
    url = f"https://api.adanos.org/{platform}/stocks/v1/stock/{ticker}"
    try:
        req = urllib.request.Request(url, headers=ADANOS_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def social_score_and_data(ticker):
    x_data   = get_adanos("x", ticker)
    news     = get_adanos("news", ticker)
    reddit   = get_adanos("reddit", ticker)
    bonus = 0
    if x_data and x_data.get("bullish_pct", 0) > 50:
        bonus += 1
    if news and news.get("sentiment_score", 0) > 0.2:
        bonus += 1
    if (x_data and x_data.get("trend") == "rising") or (news and news.get("trend") == "rising"):
        bonus += 1
    return bonus, {
        "reddit": reddit,
        "x": x_data,
        "news": news,
    }


# ── Step 6: Recovery Stage ───────────────────────────────────────────────
def classify_stage(ind):
    rsi = ind["rsi"]
    if rsi < 38 and ind["price"] < ind["ema20"]:
        return 1
    if 38 <= rsi <= 50 and ind["macd_bullish"]:
        return 2
    if rsi > 50 and ind["price"] > ind["ema20"] and ind["stoch_k"] > ind["stoch_d"]:
        return 3
    return 2  # default early recovery


# ── Step 7: Algorithmic Thesis ───────────────────────────────────────────
def build_thesis(ticker, ind, signals, stage):
    parts = []
    pba = ind["pct_below_ath"]
    rsi = ind["rsi"]
    adx = ind["adx"]
    wick = ind["lower_wick_pct"]

    if "MACD Bullish" in signals:
        parts.append("MACD histogram turned positive")
    if "RSI Rising" in signals:
        parts.append(f"RSI rising from {rsi:.0f}")
    if "Stoch K>D" in signals:
        parts.append("Stochastic K>D bullish cross")
    if "Long Wick" in signals:
        parts.append(f"long lower wick ({wick:.0f}%) signals support buying")
    if "Above EMA20" in signals:
        parts.append("price reclaimed EMA20")
    if "ADX Weak" in signals:
        parts.append(f"ADX {adx:.0f} weak trend — coiling for move")

    parts.append(f"{pba:.1f}% below 52w high")
    return ". ".join(p.capitalize() for p in parts[:4]) + "." if parts else f"Stage {stage} recovery setup — {pba:.1f}% below ATH."


# ── Score → class ────────────────────────────────────────────────────────
def score_class(total):
    if total >= 9: return "high"
    if total >= 6: return "med"
    return "low"


# ── Build card dict ──────────────────────────────────────────────────────
def build_card(ticker, ind, signals, total, stage, social_data):
    # Try to get company name from yfinance
    company = ""
    try:
        info = yf.Ticker(ticker).fast_info
        company = getattr(info, "long_name", "") or ""
    except Exception:
        pass

    def fmt_social(d):
        if not d:
            return {"bullish_pct": None, "trend": None, "sentiment_score": None}
        return {
            "bullish_pct": round(d.get("bullish_pct") or 0),
            "trend": d.get("trend"),
            "sentiment_score": round(d.get("sentiment_score") or 0, 2),
        }

    return {
        "ticker": ticker,
        "company": company,
        "score": total,
        "maxScore": 13,
        "scoreClass": score_class(total),
        "stage": stage,
        "drawdown": f"-{ind['pct_below_ath']:.1f}%",
        "rsi": f"{ind['rsi']:.0f}",
        "adx": f"{ind['adx']:.0f}",
        "macd": "Bullish ✓" if ind["macd_bullish"] else "Neutral",
        "signals": signals,
        "thesis": build_thesis(ticker, ind, signals, stage),
        "social": {
            "reddit": fmt_social(social_data.get("reddit")),
            "x": fmt_social(social_data.get("x")),
            "news": fmt_social(social_data.get("news")),
        },
    }


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    now = datetime.datetime.now()
    scan_date = now.strftime("%a %b %-d, %Y · %-I:%M%p").replace("AM","am").replace("PM","pm")

    tickers = get_candidates()
    print(f"\nStep 2-4: Analyzing {len(tickers)} candidates…\n")

    active = []
    on_deck = []
    monitor = []
    eliminated = []

    for ticker in tickers:
        print(f"  {ticker}: ", end="", flush=True)
        analysis = get_tv_analysis(ticker)
        ind = extract_indicators(analysis)
        if not ind or not ind["price"]:
            print("skip (no data)")
            continue

        passed, reasons = apply_filters(ticker, ind)
        if not passed:
            print(f"filtered ({reasons[0]})")
            eliminated.append({"ticker": ticker, "reason": reasons[0]})
            continue

        t_score, signals = tech_score(ind)
        print(f"tech={t_score}", end="")

        # Only pull social for active + on-deck (score >= 3)
        social_bonus = 0
        social_data = {}
        if t_score >= 3:
            print(" social…", end="", flush=True)
            social_bonus, social_data = social_score_and_data(ticker)
            time.sleep(0.3)  # rate limit

        total = t_score + social_bonus
        stage = classify_stage(ind)
        card = build_card(ticker, ind, signals, total, stage, social_data)

        if t_score >= 5:
            active.append(card)
            print(f" → ACTIVE (total={total})")
        elif t_score >= 3:
            on_deck.append(card)
            print(f" → ON DECK (total={total})")
        else:
            monitor.append(card)
            print(f" → MONITOR (total={total})")

    # Sort by total score desc
    active.sort(key=lambda c: c["score"], reverse=True)
    on_deck.sort(key=lambda c: c["score"], reverse=True)
    monitor.sort(key=lambda c: c["score"], reverse=True)

    result = {
        "scanDate": scan_date,
        "universe": len(tickers),
        "eliminated": len(eliminated),
        "active": active,
        "onDeck": on_deck,
        "monitor": monitor,
    }

    out_path = os.path.abspath(OUT_PATH)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n✅ Scan complete — {len(active)} active, {len(on_deck)} on deck, {len(monitor)} monitor")
    print(f"   Written → {out_path}")


if __name__ == "__main__":
    main()
