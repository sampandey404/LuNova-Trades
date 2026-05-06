#!/usr/bin/env python3
"""
Research Desk — on-demand report generator.
Usage: python generate_report.py TICKER
Triggered via GitHub Actions workflow_dispatch → commits HTML to public/tools/research-desk/.

Replicates SYSTEM_INSTRUCTIONS_v2.md data flow:
  - TradingView TA (weekly)
  - Yahoo Finance (price, analyst, options)
  - Adanos (Reddit, X, News, Polymarket)
  - StockTwits
  - Computes Tape Score + Thesis Score
  - Outputs self-contained HTML report
"""

import sys
import json
import math
import datetime
import urllib.request
import os

# ── Dependencies ─────────────────────────────────────────────────────────────
def pip_install(pkg):
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"])

try:
    from tradingview_ta import TA_Handler, Interval
except ImportError:
    pip_install("tradingview_ta"); from tradingview_ta import TA_Handler, Interval

try:
    import yfinance as yf
except ImportError:
    pip_install("yfinance"); import yfinance as yf

import requests

# ── Config ────────────────────────────────────────────────────────────────────
ADANOS_KEY = os.environ.get("ADANOS_API_KEY", "sk_live_da15ca691fc131961faf2587b8e7f0f5")
ADANOS_HDR = {"X-API-Key": ADANOS_KEY, "User-Agent": "Mozilla/5.0"}

NYSE_TICKERS = {
    "V","MA","NOW","CRM","SNOW","PLTR","ANET","MU","DELL","NET","DDOG",
    "HUBS","ZS","WDAY","LLY","UNH","OKLO","CEG","HOOD","GS","MS",
    "GE","IBM","JPM","BAC","WFC","C","AXP","DIS","KO","PG","JNJ",
    "UNP","CAT","MMM","XOM","CVX","BA","RTX","LMT","HON",
}

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "public", "tools", "research-desk")

# ── Helpers ───────────────────────────────────────────────────────────────────
def adanos(platform, ticker):
    url = f"https://api.adanos.org/{platform}/stocks/v1/stock/{ticker}"
    try:
        req = urllib.request.Request(url, headers=ADANOS_HDR)
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  Adanos {platform} failed: {e}")
        return None

def stocktwits(ticker):
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        msgs = data.get("messages", [])
        bull = sum(1 for m in msgs if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
        bear = sum(1 for m in msgs if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
        total = len(msgs)
        return {"bull": bull, "bear": bear, "total": total, "messages": msgs[:3]}
    except Exception as e:
        print(f"  StockTwits failed: {e}")
        return {"bull": 0, "bear": 0, "total": 0, "messages": []}

def tv_analysis(ticker):
    exch = "NYSE" if ticker in NYSE_TICKERS else "NASDAQ"
    for e in [exch, ("NYSE" if exch=="NASDAQ" else "NASDAQ")]:
        try:
            h = TA_Handler(symbol=ticker, exchange=e, screener="america", interval=Interval.INTERVAL_1_WEEK)
            return h.get_analysis()
        except Exception:
            continue
    return None

def score_color(s):
    if s > 65: return "#2d7a2d", "bull-color", "BULLISH"
    if s > 50: return "#2d7a2d", "bull-color", "LEANING BULLISH"
    if s > 35: return "#a32d2d", "bear-color", "LEANING BEARISH"
    return "#a32d2d", "bear-color", "BEARISH"

def dashoffset(score):
    return round(163.4 * (1 - score / 100), 1)

def analyst_label(mean):
    if mean <= 1.5: return "Strong Buy", "bull-color"
    if mean <= 2.0: return "Buy", "bull-color"
    if mean <= 2.5: return "Moderate Buy", "bull-color"
    if mean <= 3.0: return "Hold", "neutral-color"
    if mean <= 4.0: return "Moderate Sell", "bear-color"
    return "Sell", "bear-color"

def find_option_wall(options, price, kind="call"):
    """Find highest OI strike OTM for calls (above price) or puts (below price)."""
    best = None
    best_oi = 0
    for o in options:
        try:
            k = float(o.get("strike", 0))
            oi = int(o.get("openInterest", 0) or 0)
        except Exception:
            continue
        if kind == "call" and k > price and oi > best_oi:
            best_oi = oi
            best = {"strike": k, "oi": oi, "volume": o.get("volume", 0)}
        elif kind == "put" and k < price and oi > best_oi:
            best_oi = oi
            best = {"strike": k, "oi": oi, "volume": o.get("volume", 0)}
    return best or {"strike": 0, "oi": 0, "volume": 0}

def compute_tape_score(tv_rating, call_wall, put_wall, price, reddit, x_data, news, pm, st):
    """Returns Tape Score 0-100 per SYSTEM_INSTRUCTIONS_v2 Step 2."""
    # Technical component (35%)
    rating_map = {"STRONG_BUY": 10, "BUY": 6, "NEUTRAL": 0, "SELL": -6, "STRONG_SELL": -10}
    tech_contrib = rating_map.get(str(tv_rating).upper().replace(" ", "_"), 0)

    # Options Walls (30%)
    cw = call_wall["strike"] if call_wall["strike"] else price * 1.1
    pw = put_wall["strike"] if put_wall["strike"] else price * 0.9
    dist_call = abs(cw - price)
    dist_put  = abs(price - pw)
    if dist_call < dist_put:
        ratio = dist_put / dist_call if dist_call > 0 else 2
        opt_contrib = min(10, 5 + (ratio - 1) * 2)
    else:
        ratio = dist_call / dist_put if dist_put > 0 else 2
        opt_contrib = -min(10, 5 + (ratio - 1) * 2)

    # Sentiment (25%)
    sent = 0
    if x_data:
        xb = x_data.get("bullish_pct", 50)
        sent += 6 if xb > 60 else (2 if xb >= 45 else -4)
    if reddit:
        rb = reddit.get("bullish_pct", 50)
        sent += 3 if rb > 50 else (0 if rb >= 35 else -3)
    if news:
        nb = news.get("bullish_pct", 50)
        sent += 2 if nb > 60 else (-2 if nb < 40 else 0)
    if st:
        total = st["bull"] + st["bear"]
        if total > 0:
            stb = st["bull"] / total
            sent += 3 if stb > 0.6 else (-3 if stb < 0.4 else 0)

    # Polymarket (10%)
    pm_contrib = 0
    if pm:
        markets = [m for m in pm.get("markets", pm.get("data", [])) if m.get("active")]
        if markets:
            yes = markets[0].get("yes_price", 0.5)
            pm_contrib = 8 if yes > 0.65 else (4 if yes >= 0.50 else (0 if yes >= 0.35 else -8))

    weighted = tech_contrib * 0.35 + opt_contrib * 0.30 + sent * 0.25 + pm_contrib * 0.10
    tape = max(0, min(100, round(50 + weighted * 5)))
    return tape

def compute_thesis_score(rec_mean, target_mean, price, leap_top_oi, pm):
    """Returns Thesis Score 0-100 per SYSTEM_INSTRUCTIONS_v2 Step 3."""
    # Analyst consensus (40%)
    consensus = 0
    if rec_mean:
        m = float(rec_mean)
        if m <= 1.5: consensus = 8.5
        elif m <= 2.0: consensus = 6
        elif m <= 2.5: consensus = 3
        elif m <= 3.0: consensus = 0
        elif m <= 4.0: consensus = -5
        else: consensus = -9

    # Analyst upside (25%)
    upside_contrib = 0
    if target_mean and price:
        upside = (target_mean - price) / price * 100
        if upside > 50: upside_contrib = 9
        elif upside >= 25: upside_contrib = 6
        elif upside >= 10: upside_contrib = 3
        elif upside >= 0: upside_contrib = 1
        else: upside_contrib = -5

    # LEAP OI (20%)
    leap_contrib = 0
    if leap_top_oi:
        if leap_top_oi > 5000: leap_contrib = 9
        elif leap_top_oi >= 2000: leap_contrib = 6
        elif leap_top_oi >= 500: leap_contrib = 3

    # Polymarket (15%)
    pm_contrib = 0
    if pm:
        markets = [m for m in pm.get("markets", pm.get("data", [])) if m.get("active")]
        if markets:
            yes = markets[0].get("yes_price", 0.5)
            pm_contrib = 8 if yes > 0.65 else (4 if yes >= 0.50 else (0 if yes >= 0.35 else -8))

    weighted = consensus * 0.40 + upside_contrib * 0.25 + leap_contrib * 0.20 + pm_contrib * 0.15
    thesis = max(0, min(100, round(50 + weighted * 5)))
    return thesis

def divergence_badge(div):
    if div > 25:   return "div-dip",  "⚡ Fundamental Dip"
    if div < -25:  return "div-mo",   "🔥 Momentum Only"
    if div > 0:    return "div-bull", "✅ Aligned Bullish"
    if div < 0:    return "div-bear", "⚠️ Aligned Bearish"
    return "div-mixed", "~ Mixed Signal"

# ── Report builder ────────────────────────────────────────────────────────────
def generate(ticker):
    ticker = ticker.upper()
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\n🔬 Research Desk — {ticker} ({today})\n")

    # ── Data collection ────────────────────────────────────────────────────
    print("1. TradingView weekly TA…")
    tv = tv_analysis(ticker)
    tv_ind = tv.indicators if tv else {}
    tv_summary = tv.summary if tv else {}
    tv_rating = tv_summary.get("RECOMMENDATION", "NEUTRAL") if tv else "NEUTRAL"
    rsi  = round(tv_ind.get("RSI", 50) or 50, 1)
    adx  = round(tv_ind.get("ADX", 20) or 20, 1)
    ema50  = tv_ind.get("EMA50", 0)
    ema200 = tv_ind.get("EMA200", 0)

    print("2. Yahoo Finance…")
    ticker_obj = yf.Ticker(ticker)
    info = {}
    try: info = ticker_obj.info
    except Exception: pass

    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0) or 0
    high52 = info.get("fiftyTwoWeekHigh", 0) or 0
    low52  = info.get("fiftyTwoWeekLow", 0) or 0
    mktcap = info.get("marketCap", 0) or 0
    company = info.get("longName", ticker)
    target_mean = info.get("targetMeanPrice", 0) or 0
    target_high = info.get("targetHighPrice", 0) or 0
    target_low  = info.get("targetLowPrice", 0) or 0
    rec_mean = info.get("recommendationMean", 2.5) or 2.5
    analyst_count = info.get("numberOfAnalystOpinions", 0) or 0
    earnings_date = info.get("earningsDate") or info.get("nextEarningsDate")

    # Recommendations breakdown
    rec = {}
    try:
        rec_df = ticker_obj.recommendations
        if rec_df is not None and not rec_df.empty:
            last = rec_df.iloc[-1]
            rec = {
                "strongBuy": int(last.get("strongBuy", 0) or 0),
                "buy": int(last.get("buy", 0) or 0),
                "hold": int(last.get("hold", 0) or 0),
                "sell": int(last.get("sell", 0) or 0),
                "strongSell": int(last.get("strongSell", 0) or 0),
            }
    except Exception: pass

    # Options
    print("3. Options chains…")
    near_puts = []; near_calls = []
    leap_calls = []; leap_top_oi = 0
    atm_iv = 0
    try:
        exp_dates = ticker_obj.options
        if exp_dates:
            # Near expiry (first available)
            near_exp = exp_dates[0]
            chain = ticker_obj.option_chain(near_exp)
            near_calls = chain.calls.to_dict("records") if chain.calls is not None else []
            near_puts  = chain.puts.to_dict("records") if chain.puts is not None else []
            # ATM IV
            atm_calls = [c for c in near_calls if abs(float(c.get("strike",0)) - price) < price * 0.03]
            if atm_calls:
                atm_iv = round(float(atm_calls[0].get("impliedVolatility", 0) or 0) * 100, 1)
            # LEAP (any date > 180 days out)
            from datetime import date as dt
            leap_dates = [d for d in exp_dates if (datetime.datetime.strptime(d, "%Y-%m-%d").date() - dt.today()).days > 180]
            if leap_dates:
                lc = ticker_obj.option_chain(leap_dates[0]).calls.to_dict("records")
                leap_otm = [c for c in lc if float(c.get("strike",0)) > price]
                if leap_otm:
                    leap_top_oi = max(int(c.get("openInterest",0) or 0) for c in leap_otm[:20])
    except Exception as e:
        print(f"  Options failed: {e}")

    call_wall = find_option_wall(near_calls, price, "call")
    put_wall  = find_option_wall(near_puts, price, "put")

    print("4. Adanos sentiment…")
    reddit = adanos("reddit", ticker)
    x_data = adanos("x", ticker)
    news   = adanos("news", ticker)
    pm     = adanos("polymarket", ticker)

    print("5. StockTwits…")
    st = stocktwits(ticker)

    # ── Scores ────────────────────────────────────────────────────────────
    tape   = compute_tape_score(tv_rating, call_wall, put_wall, price,
                                 reddit, x_data, news, pm, st)
    thesis = compute_thesis_score(rec_mean, target_mean, price, leap_top_oi, pm)
    div    = thesis - tape
    tape_hex, tape_cls, tape_lbl   = score_color(tape)
    thesis_hex, thesis_cls, thesis_lbl = score_color(thesis)
    div_cls, div_lbl = divergence_badge(div)

    # ── Price targets ─────────────────────────────────────────────────────
    iv_frac = atm_iv / 100 if atm_iv else 0.30
    iv_move = round(price * iv_frac * math.sqrt(30/365), 2)
    near_low  = round(put_wall["strike"] or price - iv_move, 2)
    near_high = round(call_wall["strike"] or price + iv_move, 2)
    near_up_pct = 40 if tape > 50 else 30

    upside_pct = round((target_mean - price) / price * 100, 1) if target_mean and price else 0
    total_rec = max(1, sum(rec.values()) if rec else analyst_count or 1)
    bull_count = rec.get("strongBuy", 0) + rec.get("buy", 0)
    med_prob = round(min(80, (bull_count / total_rec * 100) if rec else 55))

    # Analyst bar widths
    def pct(k):
        return round(rec.get(k, 0) / total_rec * 100) if rec else 0

    # Polymarket markets
    pm_markets = []
    if pm:
        all_m = pm.get("markets", pm.get("data", []))
        pm_markets = [m for m in all_m if m.get("active")][:4]

    # StockTwits quote
    st_total = st["bull"] + st["bear"]
    st_bias = "BULLISH" if st["bull"] > st["bear"] else ("BEARISH" if st["bear"] > st["bull"] else "MIXED")
    st_cls = "bull-color" if st_bias == "BULLISH" else ("bear-color" if st_bias == "BEARISH" else "neutral-color")

    # Sentiment bar for StockTwits
    st_bar_pct = round(st["bull"] / st_total * 100) if st_total > 0 else 50
    st_bar_color = "#2d7a2d" if st_bar_pct > 50 else "#a32d2d"

    # Recent analyst upgrades/downgrades
    upgrades = []
    try:
        udf = ticker_obj.upgrades_downgrades
        if udf is not None and not udf.empty:
            udf = udf.sort_index(ascending=False)
            upgrades = udf.head(8).to_dict("records")
    except Exception: pass

    # Earnings date formatting
    earnings_str = "N/A"
    try:
        if earnings_date:
            if isinstance(earnings_date, (list, tuple)):
                ed = earnings_date[0]
            else:
                ed = earnings_date
            if hasattr(ed, "strftime"):
                earnings_str = ed.strftime("%b %d")
            else:
                earnings_str = str(ed)[:10]
    except Exception: pass

    # MA position description
    ma_pos = ""
    if ema50 and price:
        ma_pos = "above 50W EMA" if price > ema50 else "below 50W EMA"
    if ema200 and price:
        ma_pos += (" · above 200W EMA" if price > ema200 else " · below 200W EMA")

    # ── Helpers for template ──────────────────────────────────────────────
    def fmt_price(p):
        return f"{p:,.2f}" if p else "N/A"

    def sent_val(d, field="bullish_pct"):
        if not d: return 50
        return round(d.get(field, 50) or 50)

    reddit_bull = sent_val(reddit)
    reddit_bear = sent_val(reddit, "bearish_pct")
    x_bull = sent_val(x_data)
    x_bear = sent_val(x_data, "bearish_pct")
    news_bull = sent_val(news)
    news_bear = sent_val(news, "bearish_pct")

    def sent_color(bull_pct):
        return "bull-color" if bull_pct > 55 else ("bear-color" if bull_pct < 45 else "neutral-color")

    # Bull/bear scenario probabilities (Step 6)
    bull_base = pm_markets[0].get("yes_price", 0.5) * 100 if pm_markets else med_prob
    if str(tv_rating).upper() in ["BUY", "STRONG_BUY"]: bull_base = min(95, bull_base + 5)
    if (x_data or {}).get("bullish_pct", 0) > 60: bull_base = min(95, bull_base + 5)
    if call_wall["strike"] and put_wall["strike"]:
        if abs(call_wall["strike"] - price) < abs(price - put_wall["strike"]):
            bull_base = min(95, bull_base + 5)
    bull_pct = round(min(95, bull_base))
    bear_pct = round(min(95, 100 - bull_base))

    # Quotes from Adanos top_mentions
    quotes_html = ""
    quote_sources = [
        ("Reddit", reddit, "top_mentions" if reddit else None),
        ("X", x_data, "top_tweets" if x_data else None),
        ("News", news, "top_mentions" if news else None),
    ]
    q_count = 0
    for src_name, src_data, src_field in quote_sources:
        if not src_data or not src_field or q_count >= 3: continue
        items = src_data.get(src_field, [])
        if not items: continue
        item = items[0]
        text = item.get("text") or item.get("title") or item.get("body") or ""
        author = item.get("author") or item.get("handle") or item.get("source") or ""
        if not text: continue
        text = text[:200].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
        engagement = ""
        if src_name == "Reddit":
            engagement = f"r/{item.get('subreddit','?')} · {item.get('upvotes',0)} upvotes"
        elif src_name == "X":
            engagement = f"@{author} · {item.get('likes',0)} likes · {item.get('views',0)} views"
        elif src_name == "News":
            engagement = f"{item.get('source', author)}"
        quotes_html += f"""    <div class="q-row">
      <span class="q-tag">{src_name}</span>
      <div>
        <div class="q-text">"{text}"</div>
        <div class="q-eng">{engagement}</div>
      </div>
    </div>\n"""
        q_count += 1

    if not quotes_html:
        quotes_html = '    <div class="q-row"><span class="q-tag">—</span><div><div class="q-text" style="color:#aaa">No crowd quotes available for this scan.</div></div></div>'

    # Polymarket HTML
    pm_html = ""
    if pm_markets:
        for m in pm_markets:
            yes = round((m.get("yes_price", 0.5) or 0.5) * 100)
            pm_color = "#2d7a2d" if yes > 50 else "#a32d2d"
            liq = m.get("liquidity", m.get("volume", 0)) or 0
            q = (m.get("question", "") or "")[:80]
            pm_html += f"""    <div class="pm-row">
      <div class="pm-q">{q}</div>
      <div class="pm-bar-outer"><div class="pm-bar-inner" style="width:{yes}%;background:{pm_color}"></div></div>
      <div class="pm-prob" style="color:{pm_color}">{yes}% yes</div>
      <div class="pm-liq">${liq:,.0f}</div>
    </div>\n"""
    else:
        pm_html = f'    <div class="pm-empty">No active Polymarket markets found for ${ticker}.</div>'

    # Analyst recent actions HTML
    upgrades_html = ""
    for u in upgrades[:8]:
        firm = (u.get("Firm") or u.get("firm") or "—")[:22]
        grade = (u.get("ToGrade") or u.get("to_grade") or "—")
        action = (u.get("Action") or "maintained")
        pt = u.get("CurrentTarget") or u.get("current_price_target") or 0
        prior = u.get("PriorTarget") or u.get("prior_price_target") or 0
        arrow = "↑" if action.lower() in ["upgrade","raised"] else ("↓↓" if action.lower()=="downgrade" else ("↓" if "lower" in action.lower() else "="))
        g_cls = "bull-color" if any(x in grade.lower() for x in ["buy","outperform","overweight","positive"]) else ("bear-color" if any(x in grade.lower() for x in ["sell","underperform","underweight","negative"]) else "neutral-color")
        change_note = f"was ${prior:.0f}" if prior else "maintained"
        upgrades_html += f"""        <div class="ar-item">
          <div class="ar-firm">{firm}</div>
          <div class="ar-action">
            <span class="ar-grade {g_cls}">{grade} {arrow}</span>
            <span class="ar-target {g_cls}">${pt:.0f}</span>
          </div>
          <div class="ar-change">{change_note}</div>
        </div>\n"""

    if not upgrades_html:
        upgrades_html = '        <div class="ar-item"><div class="ar-firm" style="color:#aaa">No recent actions available</div></div>'

    # Bottom line
    tape_word = tape_lbl.lower()
    thesis_word = thesis_lbl.lower()
    bottom_text = (
        f"${ticker} is showing a {tape_word} tape with a {thesis_word} longer-term thesis. "
        f"Weekly TA is {tv_rating.replace('_',' ').title()} with RSI {rsi} and ADX {adx}. "
        f"Analyst consensus (mean {rec_mean:.1f}) implies {upside_pct:+.1f}% upside to the mean target of ${fmt_price(target_mean)}. "
        f"Key levels: put wall ${fmt_price(put_wall['strike'])} · call wall ${fmt_price(call_wall['strike'])}."
    )

    # ── Build HTML ────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${ticker} — Research Desk</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f0; color: #1a1a1a; padding: 2rem; min-height: 100vh; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  .header {{ display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 1.5rem; padding-bottom: 1rem; border-bottom: 1px solid #e0e0d8; }}
  .header-left .ticker {{ font-size: 26px; font-weight: 600; }}
  .header-left .company {{ font-size: 14px; color: #666; font-weight: 400; margin-left: 8px; }}
  .header-left .meta {{ font-size: 11px; color: #999; margin-top: 4px; }}
  .scores-wrap {{ display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }}
  .scores-row {{ display: flex; gap: 20px; align-items: center; }}
  .score-block {{ display: flex; align-items: center; gap: 10px; }}
  .score-ring {{ position: relative; width: 64px; height: 64px; flex-shrink: 0; }}
  .score-ring svg {{ position: absolute; top: 0; left: 0; }}
  .score-num {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); font-size: 17px; font-weight: 700; }}
  .score-info .score-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; letter-spacing: 0.07em; }}
  .score-info .score-bias {{ font-size: 15px; font-weight: 700; line-height: 1.2; }}
  .score-info .score-sub {{ font-size: 10px; color: #888; margin-top: 1px; }}
  .score-divider {{ width: 1px; height: 40px; background: #e0e0d8; }}
  .divergence-badge {{ display: inline-flex; align-items: center; gap: 5px; font-size: 10px; font-weight: 700; padding: 3px 10px; border-radius: 20px; letter-spacing: 0.05em; text-transform: uppercase; }}
  .div-dip  {{ background: #fff3cd; color: #856404; border: 1px solid #ffc107; }}
  .div-mo   {{ background: #cce5ff; color: #004085; border: 1px solid #0066cc; }}
  .div-bull {{ background: #d4edda; color: #155724; border: 1px solid #2d7a2d; }}
  .div-bear {{ background: #f8d7da; color: #721c24; border: 1px solid #a32d2d; }}
  .div-mixed{{ background: #f0f0ea; color: #666;    border: 1px solid #ccc; }}
  .bull-color {{ color: #2d7a2d; }} .bear-color {{ color: #a32d2d; }} .neutral-color {{ color: #888; }}
  .cards {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin-bottom: 1.25rem; }}
  .card {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 10px; padding: 0.65rem 0.875rem; }}
  .card-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 3px; }}
  .card-val {{ font-size: 14px; font-weight: 700; }}
  .card-sub {{ font-size: 10px; color: #888; margin-top: 2px; line-height: 1.3; }}
  .divider {{ height: 1px; background: #e8e8e0; margin: 1.25rem 0; }}
  .section-lbl {{ font-size: 9px; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 10px; }}
  .analyst-wrap {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; }}
  .analyst-top {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }}
  .analyst-count {{ font-size: 11px; color: #666; }}
  .analyst-count strong {{ color: #1a1a1a; font-size: 13px; }}
  .analyst-targets {{ display: flex; gap: 20px; }}
  .at-item {{ text-align: right; }}
  .at-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; }}
  .at-val {{ font-size: 14px; font-weight: 700; }}
  .analyst-bar-row {{ display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin-bottom: 8px; gap: 1px; }}
  .ab-sb {{ background: #1a5c1a; }} .ab-b {{ background: #2d7a2d; }} .ab-h {{ background: #e8e8e0; }} .ab-s {{ background: #a32d2d; }} .ab-ss {{ background: #6b0000; }}
  .analyst-legend {{ display: flex; gap: 14px; }}
  .al-item {{ display: flex; align-items: center; gap: 4px; font-size: 10px; color: #555; }}
  .al-dot {{ width: 8px; height: 8px; border-radius: 2px; }}
  .analyst-recent {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0f0ea; }}
  .ar-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 6px; }}
  .ar-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; }}
  .ar-item {{ background: #f8f8f5; border: 1px solid #e8e8e0; border-radius: 6px; padding: 4px 7px; }}
  .ar-firm {{ font-size: 9px; font-weight: 600; color: #555; margin-bottom: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .ar-action {{ display: flex; justify-content: space-between; align-items: center; }}
  .ar-grade {{ font-size: 9px; color: #888; }} .ar-target {{ font-size: 10px; font-weight: 700; }} .ar-change {{ font-size: 8px; color: #999; }}
  .horizon-wrap {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-bottom: 1.25rem; }}
  .hz-card {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 12px; padding: 1rem 1.125rem; }}
  .hz-card.hz-primary {{ border: 1.5px solid #e0c840; background: #fffef5; }}
  .hz-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
  .hz-label {{ font-size: 9px; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 0.08em; }}
  .hz-badge {{ font-size: 8px; font-weight: 700; padding: 1px 6px; border-radius: 10px; background: #e0c840; color: #5a4800; }}
  .hz-range {{ font-size: 22px; font-weight: 700; color: #1a1a1a; margin-bottom: 3px; }}
  .hz-prob {{ font-size: 11px; margin-bottom: 8px; }}
  .hz-bar-outer {{ height: 5px; background: #f0f0ea; border-radius: 3px; margin-bottom: 8px; overflow: hidden; }}
  .hz-bar-inner {{ height: 100%; border-radius: 3px; }}
  .hz-source {{ font-size: 10px; color: #888; margin-bottom: 6px; }}
  .hz-bullets {{ list-style: none; }}
  .hz-bullets li {{ font-size: 10px; color: #555; padding: 2px 0; border-bottom: 1px solid #f5f5f0; display: flex; justify-content: space-between; }}
  .hz-bullets li:last-child {{ border-bottom: none; }}
  .hz-val {{ font-weight: 600; color: #1a1a1a; }}
  .scenarios {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 1.25rem; }}
  .sc {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 12px; padding: 0.875rem 1.125rem; }}
  .sc-bull {{ border-left: 4px solid #2d7a2d; }} .sc-bear {{ border-left: 4px solid #a32d2d; }}
  .sc-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
  .sc-name {{ font-size: 10px; font-weight: 700; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }}
  .sc-pct {{ font-size: 24px; font-weight: 700; }}
  .sc-trigger {{ font-size: 11px; color: #666; margin-bottom: 5px; }}
  .sc-trigger strong {{ color: #1a1a1a; }}
  .sc-target {{ font-size: 12px; font-weight: 700; margin-bottom: 8px; }}
  .bar-wrap {{ height: 5px; background: #f0f0ea; border-radius: 3px; overflow: hidden; margin-bottom: 8px; }}
  .bar-fill {{ height: 100%; border-radius: 3px; }}
  .bar-bull {{ background: #2d7a2d; }} .bar-bear {{ background: #a32d2d; }}
  .tags {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .tag {{ font-size: 9px; color: #666; background: #f5f5f0; border: 1px solid #e0e0d8; border-radius: 4px; padding: 2px 6px; }}
  .sent-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 1.25rem; }}
  .sent-card {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 10px; padding: 9px 11px; }}
  .sent-src {{ font-size: 9px; color: #999; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.04em; }}
  .sent-val {{ font-size: 15px; font-weight: 700; }}
  .sent-bar-wrap {{ height: 4px; background: #f0f0ea; border-radius: 2px; margin: 5px 0 3px; overflow: hidden; }}
  .sent-bar {{ height: 100%; border-radius: 2px; background: #2d7a2d; }}
  .sent-pct {{ font-size: 9px; color: #888; }}
  .pm-list {{ margin-bottom: 1.25rem; }}
  .pm-row {{ display: flex; align-items: center; padding: 7px 0; border-bottom: 1px solid #e8e8e0; gap: 10px; }}
  .pm-row:last-child {{ border-bottom: none; }}
  .pm-q {{ font-size: 12px; color: #1a1a1a; flex: 1; }}
  .pm-bar-outer {{ width: 80px; height: 4px; background: #f0f0ea; border-radius: 2px; flex-shrink: 0; overflow: hidden; }}
  .pm-bar-inner {{ height: 100%; border-radius: 2px; }}
  .pm-prob {{ font-size: 12px; font-weight: 600; white-space: nowrap; min-width: 48px; text-align: right; }}
  .pm-liq {{ font-size: 10px; color: #999; white-space: nowrap; }}
  .pm-empty {{ font-size: 11px; color: #aaa; font-style: italic; padding: 10px 0; }}
  .quotes {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 10px; padding: 0.875rem 1rem; margin-bottom: 1.25rem; }}
  .q-row {{ display: flex; gap: 10px; padding: 6px 0; border-bottom: 1px solid #f0f0ea; }}
  .q-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .q-tag {{ font-size: 9px; font-weight: 700; color: #888; background: #f5f5f0; border: 1px solid #e0e0d8; border-radius: 4px; padding: 2px 7px; height: fit-content; white-space: nowrap; flex-shrink: 0; margin-top: 1px; }}
  .q-text {{ font-size: 11px; color: #1a1a1a; line-height: 1.5; }}
  .q-eng {{ font-size: 9px; color: #999; margin-top: 1px; }}
  .bottom-line {{ background: #fff; border: 1.5px solid #c8c8c0; border-radius: 14px; padding: 1rem 1.375rem; }}
  .bl-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
  .bl-lbl {{ font-size: 9px; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 0.08em; }}
  .bl-zone {{ font-size: 14px; font-weight: 700; }}
  .bl-text {{ font-size: 12px; color: #333; line-height: 1.75; }}
  .disclaimer {{ font-size: 9px; color: #bbb; text-align: center; margin-top: 1.5rem; }}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="header-left">
      <div class="ticker">${ticker} <span class="company">{company}</span></div>
      <div class="meta">{today} &nbsp;·&nbsp; Educational use only — not financial advice</div>
    </div>
    <div class="scores-wrap">
      <div class="scores-row">
        <div class="score-block">
          <div class="score-ring">
            <svg width="64" height="64" viewBox="0 0 64 64">
              <circle cx="32" cy="32" r="26" fill="none" stroke="#f0f0ea" stroke-width="5"/>
              <circle cx="32" cy="32" r="26" fill="none" stroke="{tape_hex}" stroke-width="5"
                stroke-dasharray="163.4" stroke-dashoffset="{dashoffset(tape)}"
                stroke-linecap="round" transform="rotate(-90 32 32)"/>
            </svg>
            <div class="score-num {tape_cls}">{tape}</div>
          </div>
          <div class="score-info">
            <div class="score-lbl">Tape Score</div>
            <div class="score-bias {tape_cls}">{tape_lbl}</div>
            <div class="score-sub">Near-term · Weekly TA</div>
          </div>
        </div>
        <div class="score-divider"></div>
        <div class="score-block">
          <div class="score-ring">
            <svg width="64" height="64" viewBox="0 0 64 64">
              <circle cx="32" cy="32" r="26" fill="none" stroke="#f0f0ea" stroke-width="5"/>
              <circle cx="32" cy="32" r="26" fill="none" stroke="{thesis_hex}" stroke-width="5"
                stroke-dasharray="163.4" stroke-dashoffset="{dashoffset(thesis)}"
                stroke-linecap="round" transform="rotate(-90 32 32)"/>
            </svg>
            <div class="score-num {thesis_cls}">{thesis}</div>
          </div>
          <div class="score-info">
            <div class="score-lbl">Thesis Score</div>
            <div class="score-bias {thesis_cls}">{thesis_lbl}</div>
            <div class="score-sub">Medium / LEAP</div>
          </div>
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;align-items:center;">
        <span style="font-size:9px;color:#999;">Δ {div:+d} pts</span>
        <div class="divergence-badge {div_cls}">{div_lbl}</div>
      </div>
    </div>
  </div>

  <div class="cards">
    <div class="card">
      <div class="card-lbl">Price</div>
      <div class="card-val">${fmt_price(price)}</div>
      <div class="card-sub">52W ${fmt_price(low52)} – ${fmt_price(high52)}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Weekly TA</div>
      <div class="card-val {tape_cls}">{tv_rating.replace('_',' ').title()}</div>
      <div class="card-sub">RSI {rsi} · ADX {adx} · {ma_pos[:30]}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Analyst Rating</div>
      <div class="card-val {analyst_label(rec_mean)[1]}">{analyst_label(rec_mean)[0]}</div>
      <div class="card-sub">{analyst_count} analysts · mean {rec_mean:.1f}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Call Wall</div>
      <div class="card-val">${fmt_price(call_wall['strike'])}</div>
      <div class="card-sub">OI {call_wall['oi']:,}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Put Wall</div>
      <div class="card-val">${fmt_price(put_wall['strike'])}</div>
      <div class="card-sub">OI {put_wall['oi']:,}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Next Earnings</div>
      <div class="card-val">{earnings_str}</div>
      <div class="card-sub">est.</div>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Analyst Coverage — {analyst_count} firms</div>
  <div class="analyst-wrap">
    <div class="analyst-top">
      <div>
        <div class="analyst-count"><strong>{bull_count} of {total_rec}</strong> analysts bullish</div>
      </div>
      <div class="analyst-targets">
        <div class="at-item"><div class="at-lbl">Low</div><div class="at-val bear-color">${fmt_price(target_low)}</div></div>
        <div class="at-item"><div class="at-lbl">Analyst Mean</div><div class="at-val">${fmt_price(target_mean)}</div></div>
        <div class="at-item"><div class="at-lbl">High</div><div class="at-val bull-color">${fmt_price(target_high)}</div></div>
      </div>
    </div>
    <div class="analyst-bar-row">
      <div class="ab-sb" style="width:{pct('strongBuy')}%"></div>
      <div class="ab-b"  style="width:{pct('buy')}%"></div>
      <div class="ab-h"  style="width:{pct('hold')}%"></div>
      <div class="ab-s"  style="width:{pct('sell')}%"></div>
      <div class="ab-ss" style="width:{pct('strongSell')}%"></div>
    </div>
    <div class="analyst-legend">
      <div class="al-item"><div class="al-dot" style="background:#1a5c1a"></div> Strong Buy {rec.get('strongBuy',0)}</div>
      <div class="al-item"><div class="al-dot" style="background:#2d7a2d"></div> Buy {rec.get('buy',0)}</div>
      <div class="al-item"><div class="al-dot" style="background:#e8e8e0"></div> Hold {rec.get('hold',0)}</div>
      <div class="al-item"><div class="al-dot" style="background:#a32d2d"></div> Sell {rec.get('sell',0)}</div>
    </div>
    <div class="analyst-recent">
      <div class="ar-lbl">Recent analyst actions</div>
      <div class="ar-grid">
{upgrades_html}      </div>
    </div>
  </div>

  <div class="section-lbl">Price Target Horizon</div>
  <div class="horizon-wrap">
    <div class="hz-card">
      <div class="hz-header"><div class="hz-label">Near-Term · 4 weeks</div></div>
      <div class="hz-range">${fmt_price(near_low)} – ${fmt_price(near_high)}</div>
      <div class="hz-prob {tape_cls}">{near_up_pct}% up · {100-near_up_pct}% down</div>
      <div class="hz-bar-outer"><div class="hz-bar-inner" style="width:{near_up_pct}%;background:{tape_hex}"></div></div>
      <div class="hz-source">IV-implied ±${iv_move} · put/call walls</div>
      <ul class="hz-bullets">
        <li><span>Put wall floor</span><span class="hz-val bear-color">${fmt_price(put_wall['strike'])}</span></li>
        <li><span>Call wall ceiling</span><span class="hz-val">${fmt_price(call_wall['strike'])}</span></li>
        <li><span>ATM IV</span><span class="hz-val">{atm_iv}%</span></li>
      </ul>
    </div>
    <div class="hz-card hz-primary">
      <div class="hz-header">
        <div class="hz-label">Medium · EOY / 12-month</div>
        <div class="hz-badge">PRIMARY</div>
      </div>
      <div class="hz-range {thesis_cls}">${fmt_price(target_low)} – ${fmt_price(target_high)}</div>
      <div class="hz-prob {thesis_cls}">{med_prob}% probability</div>
      <div class="hz-bar-outer"><div class="hz-bar-inner" style="width:{med_prob}%;background:{thesis_hex}"></div></div>
      <div class="hz-source">{analyst_count} analysts</div>
      <ul class="hz-bullets">
        <li><span>Analyst Mean</span><span class="hz-val">${fmt_price(target_mean)}</span></li>
        <li><span>Upside to mean</span><span class="hz-val {thesis_cls}">{upside_pct:+.1f}%</span></li>
        <li><span>Low / High</span><span class="hz-val">${fmt_price(target_low)} / ${fmt_price(target_high)}</span></li>
      </ul>
    </div>
    <div class="hz-card">
      <div class="hz-header"><div class="hz-label">LEAP · Structural</div></div>
      {"<div class='hz-range bull-color'>See LEAP chain</div><div class='hz-prob bull-color'>Top OI: " + f"{leap_top_oi:,}" + " contracts</div>" if leap_top_oi else "<div class='hz-no-data' style='font-size:11px;color:#aaa;font-style:italic;padding:8px 0'>No LEAP data available</div>"}
      <ul class="hz-bullets">
        <li><span>LEAP top OI</span><span class="hz-val bull-color">{leap_top_oi:,}</span></li>
      </ul>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Key tape scenarios</div>
  <div class="scenarios">
    <div class="sc sc-bull">
      <div class="sc-top">
        <div class="sc-name">Bull tape</div>
        <div class="sc-pct bull-color">{bull_pct}%</div>
      </div>
      <div class="sc-trigger">Reclaim <strong>${fmt_price(call_wall['strike'])} on weekly close</strong></div>
      <div class="sc-target bull-color">Target: ${fmt_price(near_high)} near · ${fmt_price(target_mean)} medium</div>
      <div class="bar-wrap"><div class="bar-fill bar-bull" style="width:{bull_pct}%"></div></div>
      <div class="tags">
        <span class="tag">Weekly TA: {tv_rating.replace('_',' ')}</span>
        <span class="tag">RSI {rsi}</span>
        <span class="tag">X bull {x_bull}%</span>
      </div>
    </div>
    <div class="sc sc-bear">
      <div class="sc-top">
        <div class="sc-name">Bear tape</div>
        <div class="sc-pct bear-color">{bear_pct}%</div>
      </div>
      <div class="sc-trigger">Lose <strong>${fmt_price(put_wall['strike'])} on weekly close</strong></div>
      <div class="sc-target bear-color">Target: ${fmt_price(near_low)} near · 52W low ${fmt_price(low52)}</div>
      <div class="bar-wrap"><div class="bar-fill bar-bear" style="width:{bear_pct}%"></div></div>
      <div class="tags">
        <span class="tag">ADX {adx}</span>
        <span class="tag">Reddit bull {reddit_bull}%</span>
        <span class="tag">Put wall ${fmt_price(put_wall['strike'])}</span>
      </div>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Sentiment scoreboard</div>
  <div class="sent-grid">
    <div class="sent-card">
      <div class="sent-src">Reddit</div>
      <div class="sent-val {sent_color(reddit_bull)}">{reddit_bull}%</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{reddit_bull}%"></div></div>
      <div class="sent-pct">{reddit_bull}% bull · {reddit_bear}% bear</div>
    </div>
    <div class="sent-card">
      <div class="sent-src">X / Twitter</div>
      <div class="sent-val {sent_color(x_bull)}">{x_bull}%</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{x_bull}%"></div></div>
      <div class="sent-pct">{x_bull}% bull · {x_bear}% bear</div>
    </div>
    <div class="sent-card">
      <div class="sent-src">News</div>
      <div class="sent-val {sent_color(news_bull)}">{news_bull}%</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{news_bull}%"></div></div>
      <div class="sent-pct">{news_bull}% bull · {news_bear}% bear</div>
    </div>
    <div class="sent-card">
      <div class="sent-src">Polymarket</div>
      <div class="sent-val">{"Active" if pm_markets else "—"}</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{round(pm_markets[0].get('yes_price',0.5)*100) if pm_markets else 50}%"></div></div>
      <div class="sent-pct">{len(pm_markets)} active market{"s" if len(pm_markets)!=1 else ""}</div>
    </div>
    <div class="sent-card">
      <div class="sent-src">StockTwits</div>
      <div class="sent-val {st_cls}">{st_bias}</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{st_bar_pct}%;background:{st_bar_color}"></div></div>
      <div class="sent-pct">{st['bull']} bull · {st['bear']} bear · {st['total']} msgs</div>
    </div>
  </div>

  <div class="quotes">
    <div class="section-lbl" style="margin-bottom:8px">What the crowd is saying</div>
{quotes_html}  </div>

  <div class="section-lbl">Polymarket active bets</div>
  <div class="pm-list">
{pm_html}  </div>

  <div class="bottom-line">
    <div class="bl-top">
      <div class="bl-lbl">Bottom line</div>
      <div class="bl-zone">
        <span class="{tape_cls}">Tape: {tape_word}</span>
        &nbsp;·&nbsp;
        <span class="{thesis_cls}">Thesis: {thesis_word}</span>
      </div>
    </div>
    <div class="bl-text">{bottom_text}</div>
  </div>

  <div class="disclaimer">For educational and research purposes only. Not financial advice. All data sourced from public APIs.</div>
</div>
</body>
</html>"""

    # ── Write file ────────────────────────────────────────────────────────
    out_path = os.path.abspath(os.path.join(OUT_DIR, f"{ticker}_{today}.html"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)

    print(f"\n✅ Report written → {out_path}")
    print(f"   Tape: {tape}/100 — {tape_lbl} | Thesis: {thesis}/100 — {thesis_lbl}")
    print(f"   Signal: {div_lbl} (Δ {div:+d} pts)")
    return out_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_report.py TICKER")
        sys.exit(1)
    generate(sys.argv[1])
