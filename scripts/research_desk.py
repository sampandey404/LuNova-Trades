#!/usr/bin/env python3
"""
Research Desk — generates a self-contained HTML report for a given ticker.
Usage: python scripts/research_desk.py TICKER
Outputs:
  public/tools/research-desk/TICKER_YYYY-MM-DD.html
  public/tools/research-desk/manifest.json  (updated)
"""
import json, math, os, sys, datetime, requests
import yfinance as yf
import pandas as pd
import ta as ta_lib

ADANOS_KEY  = os.environ.get('ADANOS_API_KEY', '')
GEMINI_KEY  = os.environ.get('GEMINI_API_KEY', '')
ADANOS_BASE = 'https://api.adanos.org'
OUT_DIR     = os.path.join(os.path.dirname(__file__), '..', 'public', 'tools', 'research-desk')

# ── Helpers ────────────────────────────────────────────────────────────────────

def safe(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict): return default
        d = d.get(k, default)
    return d

def adanos(platform, ticker):
    if not ADANOS_KEY: return {}
    try:
        r = requests.get(f'{ADANOS_BASE}/{platform}/stocks/v1/stock/{ticker}',
                         headers={'X-API-Key': ADANOS_KEY}, timeout=12)
        return r.json() if r.ok else {}
    except Exception:
        return {}

def stocktwits(ticker):
    try:
        r = requests.get(f'https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json', timeout=10)
        if not r.ok: return {'bull': 0, 'bear': 0, 'total': 0, 'top': []}
        msgs = r.json().get('messages', [])
        bull = sum(1 for m in msgs if safe(m, 'entities', 'sentiment', 'basic') == 'Bullish')
        bear = sum(1 for m in msgs if safe(m, 'entities', 'sentiment', 'basic') == 'Bearish')
        top = [{'text': m.get('body',''), 'user': m.get('user',{}).get('username',''),
                'sentiment': safe(m,'entities','sentiment','basic') or 'Neutral'}
               for m in msgs[:3] if m.get('body')]
        return {'bull': bull, 'bear': bear, 'total': len(msgs), 'top': top}
    except Exception:
        return {'bull': 0, 'bear': 0, 'total': 0, 'top': []}

def score_color(score):
    if score >= 65: return '#2d7a2d'
    if score >= 50: return '#888'
    return '#a32d2d'

def score_label(score):
    if score >= 65: return 'BULLISH'
    if score >= 55: return 'LEANING BULLISH'
    if score >= 45: return 'NEUTRAL'
    if score >= 35: return 'LEANING BEARISH'
    return 'BEARISH'

def score_class(score):
    if score >= 65: return 'bull-color'
    if score >= 50: return 'neutral-color'
    return 'bear-color'

def ring_offset(score):
    return round(163.4 * (1 - score / 100), 1)

def divergence_badge(tape, thesis):
    d = thesis - tape
    if d > 25:    return 'div-dip',  f'⚡ Fundamental Dip — Δ{abs(d)} pts'
    if d < -25:   return 'div-mo',   f'🔥 Momentum Only — Δ{abs(d)} pts'
    if tape > 65 and thesis > 65: return 'div-bull', f'✅ Aligned Bullish — Δ{abs(d)} pts'
    if tape < 35 and thesis < 35: return 'div-bear', f'⚠️ Aligned Bearish — Δ{abs(d)} pts'
    return 'div-mixed', f'~ Mixed Signal — Δ{abs(d)} pts'

def pct_fmt(v): return f'{v:+.1f}%' if v is not None else '—'

def dollars(v): return f'${v:,.2f}' if v else '—'

# ── Data collection ────────────────────────────────────────────────────────────

def collect(ticker):
    tk = yf.Ticker(ticker)
    info = {}
    try: info = tk.info
    except Exception: pass

    # Price & basics
    price = (info.get('currentPrice') or info.get('regularMarketPrice') or
             info.get('previousClose') or 0)
    company = info.get('longName', ticker)
    exchange = info.get('exchange', 'NASDAQ')
    w52_high = info.get('fiftyTwoWeekHigh', 0)
    w52_low  = info.get('fiftyTwoWeekLow', 0)
    mkt_cap  = info.get('marketCap', 0)
    target_mean = info.get('targetMeanPrice', 0)
    target_high = info.get('targetHighPrice', 0)
    target_low  = info.get('targetLowPrice', 0)
    rec_mean    = info.get('recommendationMean', 3.0)
    num_analysts = info.get('numberOfAnalystOpinions', 0)

    # Earnings date
    earnings_str = '—'
    try:
        ed = tk.calendar
        if isinstance(ed, dict):
            ed_val = ed.get('Earnings Date', [None])[0]
        elif hasattr(ed, 'iloc'):
            ed_val = ed.iloc[0, 0] if not ed.empty else None
        else:
            ed_val = None
        if ed_val:
            earnings_str = pd.Timestamp(ed_val).strftime('%b %d')
    except Exception:
        pass

    # Analyst recommendations
    rec_counts = {'strongBuy': 0, 'buy': 0, 'hold': 0, 'sell': 0, 'strongSell': 0}
    try:
        recs = tk.recommendations
        if recs is not None and not recs.empty:
            latest = recs.iloc[-1]
            for k in rec_counts:
                rec_counts[k] = int(latest.get(k, 0) or 0)
    except Exception:
        pass

    # Upgrades/downgrades
    upgrades = []
    try:
        ud = tk.upgrades_downgrades
        if ud is not None and not ud.empty:
            ud = ud.sort_index(ascending=False).head(8)
            for idx, row in ud.iterrows():
                upgrades.append({
                    'firm': str(row.get('Firm', '')),
                    'action': str(row.get('Action', '')),
                    'to_grade': str(row.get('ToGrade', '')),
                    'from_grade': str(row.get('FromGrade', '')),
                    'date': str(idx)[:10] if idx else '',
                })
    except Exception:
        pass

    # Weekly TA via pandas-ta
    rsi_val = adx_val = macdh_val = ema20_val = ema200_val = None
    try:
        hist = tk.history(period='1y', interval='1wk')
        if not hist.empty and len(hist) >= 20:
            def fv(s): v = s.iloc[-1]; return float(v) if v is not None and not pd.isna(v) else None
            rsi_val    = fv(ta_lib.momentum.RSIIndicator(hist['Close'], window=14).rsi())
            macdh_val  = fv(ta_lib.trend.MACD(hist['Close']).macd_diff())
            adx_val    = fv(ta_lib.trend.ADXIndicator(hist['High'], hist['Low'], hist['Close'], window=14).adx())
            ema20_val  = fv(ta_lib.trend.EMAIndicator(hist['Close'], window=20).ema_indicator())
            ema200_val = fv(ta_lib.trend.EMAIndicator(hist['Close'], window=200).ema_indicator())
    except Exception:
        pass

    # TA rating
    ta_signals = []
    if rsi_val: ta_signals.append(f'RSI {rsi_val:.1f}')
    if adx_val: ta_signals.append(f'ADX {adx_val:.1f}')
    if ema20_val and price:
        ta_signals.append('Above EMA20' if price > ema20_val else 'Below EMA20')
    if ema200_val and price:
        ta_signals.append('Above EMA200' if price > ema200_val else 'Below EMA200')
    ta_str = ' · '.join(ta_signals) or '—'

    # Options — near expiry for walls, LEAP for OI
    call_wall = put_wall = call_wall_oi = put_wall_oi = 0
    leap_strike = leap_oi = 0
    near_iv = 0
    try:
        expiries = tk.options
        today = datetime.date.today()
        near_exp = None
        for e in expiries:
            d = datetime.date.fromisoformat(e)
            if (d - today).days >= 3:
                near_exp = e
                break

        if near_exp:
            chain = tk.option_chain(near_exp)
            calls = chain.calls
            puts  = chain.puts

            if not calls.empty and price:
                otm_calls = calls[calls['strike'] > price]
                if not otm_calls.empty:
                    idx = otm_calls['openInterest'].idxmax()
                    call_wall    = float(otm_calls.loc[idx, 'strike'])
                    call_wall_oi = int(otm_calls.loc[idx, 'openInterest'])
                    near_iv = float(otm_calls.loc[idx, 'impliedVolatility'] or 0)

            if not puts.empty and price:
                otm_puts = puts[puts['strike'] < price]
                if not otm_puts.empty:
                    idx = otm_puts['openInterest'].idxmax()
                    put_wall    = float(otm_puts.loc[idx, 'strike'])
                    put_wall_oi = int(otm_puts.loc[idx, 'openInterest'])

        # LEAP
        leap_dates = [e for e in expiries if (datetime.date.fromisoformat(e) - today).days > 180]
        if leap_dates and price:
            lchain = tk.option_chain(leap_dates[0]).calls
            otm = lchain[lchain['strike'] > price]
            if not otm.empty:
                idx = otm['openInterest'].idxmax()
                leap_strike = float(otm.loc[idx, 'strike'])
                leap_oi     = int(otm.loc[idx, 'openInterest'])
    except Exception as e:
        print(f'Options error: {e}')

    # Social
    x_data      = adanos('x',        ticker)
    news_data   = adanos('news',      ticker)
    reddit_data = adanos('reddit',    ticker)
    poly_data   = adanos('polymarket',ticker)
    st_data     = stocktwits(ticker)

    return {
        'ticker': ticker, 'company': company, 'exchange': exchange,
        'price': price, 'w52_high': w52_high, 'w52_low': w52_low,
        'mkt_cap': mkt_cap, 'target_mean': target_mean,
        'target_high': target_high, 'target_low': target_low,
        'rec_mean': rec_mean, 'num_analysts': num_analysts,
        'earnings_str': earnings_str,
        'rec_counts': rec_counts, 'upgrades': upgrades,
        'rsi': rsi_val, 'adx': adx_val, 'macdh': macdh_val,
        'ema20': ema20_val, 'ema200': ema200_val, 'ta_str': ta_str,
        'call_wall': call_wall, 'call_wall_oi': call_wall_oi,
        'put_wall': put_wall, 'put_wall_oi': put_wall_oi,
        'near_iv': near_iv, 'leap_strike': leap_strike, 'leap_oi': leap_oi,
        'x': x_data, 'news': news_data, 'reddit': reddit_data,
        'polymarket': poly_data, 'stocktwits': st_data,
    }

# ── Scoring ────────────────────────────────────────────────────────────────────

def compute_scores(d):
    price = d['price']

    # ── Tape Score ──
    # Technical (35%): derive simple signal from TA indicators
    tech_contrib = 0
    rsi = d['rsi']; adx = d['adx']; macdh = d['macdh']
    ema20 = d['ema20']; ema200 = d['ema200']
    if rsi and macdh is not None and ema20 and ema200 and price:
        above_ema20  = price > ema20
        above_ema200 = price > ema200
        macd_bull    = macdh > 0
        rsi_ok       = 40 <= rsi <= 70
        if above_ema200 and macd_bull and rsi_ok and above_ema20:
            tech_contrib = 8
        elif above_ema200 and macd_bull:
            tech_contrib = 4
        elif above_ema200:
            tech_contrib = 0
        elif macd_bull:
            tech_contrib = -2
        else:
            tech_contrib = -6

    # Options walls (30%)
    opts_contrib = 0
    if d['call_wall'] and d['put_wall'] and price:
        call_dist = d['call_wall'] - price
        put_dist  = price - d['put_wall']
        if call_dist > 0 and put_dist > 0:
            ratio = put_dist / call_dist
            if ratio > 2:   opts_contrib = -8
            elif ratio > 1: opts_contrib = -4
            elif ratio > 0.7: opts_contrib = 2
            else: opts_contrib = 7

    # Sentiment (25%)
    x_bull = float(d['x'].get('bullish_pct', 50) or 50)
    st = d['stocktwits']
    st_total = st['bull'] + st['bear']
    st_bull_pct = st['bull'] / st_total * 100 if st_total > 0 else 50
    news_sent = float(d['news'].get('sentiment_score', 0) or 0)
    reddit_bull = float(d['reddit'].get('bullish_pct', 50) or 50)

    x_contrib = 6 if x_bull > 60 else (2 if x_bull >= 45 else -4)
    reddit_contrib = 3 if reddit_bull > 50 else (0 if reddit_bull >= 35 else -3)
    news_contrib = 2 if news_sent > 0 else -2
    st_contrib = 3 if st_bull_pct > 55 else (0 if st_bull_pct >= 45 else -3)
    sent_contrib = (x_contrib + reddit_contrib + news_contrib + st_contrib) / 4 * 4  # average * 4

    # Polymarket (10%)
    poly_contrib = 0
    try:
        markets = [m for m in (d['polymarket'].get('markets') or []) if m.get('active')]
        if markets:
            yes_price = float(markets[0].get('yes_price', 0.5) or 0.5)
            poly_contrib = 8 if yes_price > 0.65 else (4 if yes_price >= 0.50 else (0 if yes_price >= 0.35 else -8))
    except Exception:
        pass

    tape_raw = (tech_contrib * 0.35 + opts_contrib * 0.30 +
                sent_contrib * 0.25 + poly_contrib * 0.10)
    tape = min(100, max(0, round(50 + tape_raw * 5)))

    # ── Thesis Score ──
    # Analyst consensus (40%)
    rm = d['rec_mean'] or 3.0
    if rm <= 1.5:   cons_contrib = 8.5
    elif rm <= 2.0: cons_contrib = 6
    elif rm <= 2.5: cons_contrib = 3
    elif rm <= 3.0: cons_contrib = 0
    elif rm <= 4.0: cons_contrib = -5
    else:           cons_contrib = -9

    # Analyst upside (25%)
    upside_contrib = 0
    if d['target_mean'] and price:
        upside = (d['target_mean'] - price) / price * 100
        if upside > 50:   upside_contrib = 9
        elif upside > 25: upside_contrib = 6
        elif upside > 10: upside_contrib = 3
        elif upside > 0:  upside_contrib = 1
        else:             upside_contrib = -5

    # LEAP OI (20%)
    leap_contrib = 0
    if d['leap_oi'] > 5000:     leap_contrib = 9
    elif d['leap_oi'] > 2000:   leap_contrib = 6
    elif d['leap_oi'] > 500:    leap_contrib = 3

    thesis_raw = (cons_contrib * 0.40 + upside_contrib * 0.25 +
                  leap_contrib * 0.20 + poly_contrib * 0.15)
    thesis = min(100, max(0, round(50 + thesis_raw * 5)))

    return tape, thesis

# ── Gemini bottom line ─────────────────────────────────────────────────────────

def gemini_bottom_line(d, tape, thesis):
    if not GEMINI_KEY:
        return (f"Tape Score {tape}/100 ({score_label(tape).title()}) with Thesis Score "
                f"{thesis}/100 ({score_label(thesis).title()}). "
                f"Key technical levels: call wall ${d['call_wall']:.0f}, put wall ${d['put_wall']:.0f}. "
                f"Not financial advice.")
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        upside = ((d['target_mean'] - d['price']) / d['price'] * 100
                  if d['target_mean'] and d['price'] else 0)
        prompt = (
            f"Write exactly 3 sentences summarizing ${d['ticker']} ({d['company']}) for a quant research desk.\n"
            f"Tape Score: {tape}/100 ({score_label(tape)})\n"
            f"Thesis Score: {thesis}/100 ({score_label(thesis)})\n"
            f"RSI: {d['rsi']:.1f if d['rsi'] else '—'}, ADX: {d['adx']:.1f if d['adx'] else '—'}\n"
            f"Analyst mean target: ${d['target_mean']:.2f} ({upside:+.1f}% upside)\n"
            f"Call wall: ${d['call_wall']:.0f}, Put wall: ${d['put_wall']:.0f}\n"
            f"Lead with what the tape is doing (near-term bias), then thesis conviction, then key levels to watch. "
            f"Be factual. No financial advice. No markdown."
        )
        return model.generate_content(prompt).text.strip()
    except Exception as e:
        print(f'Gemini error: {e}')
        return (f"Tape Score {tape}/100 ({score_label(tape).title()}), Thesis Score {thesis}/100 "
                f"({score_label(thesis).title()}). "
                f"Key levels: call wall ${d['call_wall']:.0f}, put wall ${d['put_wall']:.0f}. "
                f"Not financial advice.")

# ── HTML generation ────────────────────────────────────────────────────────────

def rec_mean_label(rm):
    if rm <= 1.5: return 'Strong Buy'
    if rm <= 2.5: return 'Buy'
    if rm <= 3.5: return 'Hold'
    if rm <= 4.5: return 'Sell'
    return 'Strong Sell'

def generate_html(d, tape, thesis, bottom_line, report_date):
    ticker  = d['ticker']
    company = d['company']
    price   = d['price']

    div_cls, div_txt = divergence_badge(tape, thesis)
    tape_color   = score_color(tape)
    thesis_color = score_color(thesis)
    tape_lbl     = score_label(tape)
    thesis_lbl   = score_label(thesis)
    tape_cls     = score_class(tape)
    thesis_cls   = score_class(thesis)
    tape_off     = ring_offset(tape)
    thesis_off   = ring_offset(thesis)

    # Analyst bar
    rc = d['rec_counts']
    total_rc = sum(rc.values()) or 1
    def pct(k): return round(rc[k] / total_rc * 100, 1)

    # Sentiment
    x_bull = float(d['x'].get('bullish_pct', 50) or 50)
    reddit_bull = float(d['reddit'].get('bullish_pct', 50) or 50)
    news_sent = float(d['news'].get('sentiment_score', 0) or 0)
    news_bull = min(100, max(0, round((news_sent + 1) / 2 * 100)))
    st = d['stocktwits']
    st_total = st['bull'] + st['bear']
    st_bull_pct = round(st['bull'] / st_total * 100) if st_total > 0 else 50

    # Polymarket
    poly_rows = ''
    try:
        markets = [m for m in (d['polymarket'].get('markets') or []) if m.get('active')][:4]
        for m in markets:
            yp = float(m.get('yes_price', 0.5) or 0.5)
            yp_pct = round(yp * 100)
            bar_color = '#2d7a2d' if yp > 0.55 else ('#a32d2d' if yp < 0.45 else '#888')
            liq = m.get('liquidity') or m.get('volume') or 0
            liq_str = f'${liq:,.0f} liq' if liq else ''
            poly_rows += (f'<div class="pm-row"><div class="pm-q">{m.get("question","")}</div>'
                          f'<div class="pm-bar-outer"><div class="pm-bar-inner" style="width:{yp_pct}%;background:{bar_color}"></div></div>'
                          f'<div class="pm-prob" style="color:{bar_color}">{yp_pct}%</div>'
                          f'<div class="pm-liq">{liq_str}</div></div>')
    except Exception:
        pass
    if not poly_rows:
        poly_rows = '<div style="font-size:12px;color:#999;padding:8px 0;">No active Polymarket data for this ticker.</div>'

    # Scenarios
    bull_prob = min(80, max(15, tape))
    bear_prob = 100 - bull_prob
    bull_target_lo = round(d['call_wall'], 2) if d['call_wall'] else round(price * 1.08, 2)
    bull_target_hi = round(d['call_wall'] * 1.05, 2) if d['call_wall'] else round(price * 1.15, 2)
    bear_target = round(d['put_wall'] * 0.97, 2) if d['put_wall'] else round(price * 0.88, 2)
    bull_trigger = (f'Reclaim <strong>${d["call_wall"]:.0f} call wall</strong> on weekly close'
                    if d['call_wall'] else 'Break above recent highs with volume')
    bear_trigger = (f'Break below <strong>${d["put_wall"]:.0f} put wall</strong> on weekly close'
                    if d['put_wall'] else 'Fail at current resistance with volume expansion')
    above_ema20  = d['ema20']  and price and price > d['ema20']
    above_ema200 = d['ema200'] and price and price > d['ema200']
    ta_signals_list = []
    if d['rsi'] and d['rsi'] > 50: ta_signals_list.append('RSI > 50')
    if d['macdh'] and d['macdh'] > 0: ta_signals_list.append('MACD Positive')
    if above_ema20: ta_signals_list.append('Above EMA20')
    if above_ema200: ta_signals_list.append('Above EMA200')
    bear_signals_list = ['Vol Expansion', 'RSI Rollover', 'MACD Cross']
    bull_tags = ''.join(f'<span class="tag">{s}</span>' for s in (ta_signals_list or ['TA Momentum']))
    bear_tags = ''.join(f'<span class="tag">{s}</span>' for s in bear_signals_list[:3])
    scenarios_html = f"""
    <div class="sc sc-bull">
      <div class="sc-label">Bull Case</div>
      <div class="sc-pct bull-color">{bull_prob}%</div>
      <div class="sc-trigger">{bull_trigger}</div>
      <div class="sc-target bull-color">Target: ${bull_target_lo:.0f} – ${bull_target_hi:.0f}</div>
      <div class="bar-wrap"><div class="bar-fill bar-bull" style="width:{bull_prob}%"></div></div>
      <div class="tags">{bull_tags}</div>
    </div>
    <div class="sc sc-bear">
      <div class="sc-label">Bear Case</div>
      <div class="sc-pct bear-color">{bear_prob}%</div>
      <div class="sc-trigger">{bear_trigger}</div>
      <div class="sc-target bear-color">Target: ${bear_target:.0f}</div>
      <div class="bar-wrap"><div class="bar-fill bar-bear" style="width:{bear_prob}%"></div></div>
      <div class="tags">{bear_tags}</div>
    </div>"""

    # Quotes
    quotes_html = ''
    for msg in (d['stocktwits'].get('top') or []):
        text = str(msg.get('text', '')).replace('<','&lt;').replace('>','&gt;')[:200]
        user = msg.get('user', '')
        sent = msg.get('sentiment', 'Neutral')
        quotes_html += (f'<div class="q-row"><span class="q-tag">StockTwits</span>'
                        f'<div><div class="q-text">"{text}"</div>'
                        f'<div class="q-eng">@{user} &nbsp;·&nbsp; {sent}</div></div></div>')
    news_headlines = (d['news'].get('articles') or d['news'].get('headlines') or [])[:2]
    for h in news_headlines:
        title = str(h.get('title') or h.get('headline') or '').replace('<','&lt;').replace('>','&gt;')[:180]
        src = h.get('source') or h.get('publisher') or 'News'
        if title:
            quotes_html += (f'<div class="q-row"><span class="q-tag">{src}</span>'
                            f'<div><div class="q-text">{title}</div></div></div>')
    if not quotes_html:
        quotes_html = '<div style="font-size:11px;color:#999;padding:8px 0;">No recent quotes or headlines available.</div>'

    # Bottom line badge colors
    tape_badge_bg = '#d4edda' if tape >= 65 else ('#f8d7da' if tape < 35 else '#f0f0ea')
    tape_badge_color = '#155724' if tape >= 65 else ('#721c24' if tape < 35 else '#555')
    thesis_badge_bg = '#d4edda' if thesis >= 65 else ('#f8d7da' if thesis < 35 else '#f0f0ea')
    thesis_badge_color = '#155724' if thesis >= 65 else ('#721c24' if thesis < 35 else '#555')

    # Upgrades/downgrades
    ud_html = ''
    for u in d['upgrades'][:4]:
        action_sym = '↑' if 'up' in u['action'].lower() else ('↓' if 'down' in u['action'].lower() else '=')
        cls = 'bull-color' if '↑' == action_sym else ('bear-color' if '↓' == action_sym else 'neutral-color')
        ud_html += (f'<div class="ar-item"><div class="ar-firm">{u["firm"]}</div>'
                    f'<div class="ar-action"><span class="ar-grade {cls}">{u["to_grade"]} {action_sym}</span></div>'
                    f'<div class="ar-change">{u["date"]}</div></div>')
    if not ud_html:
        ud_html = '<div style="font-size:11px;color:#999;">No recent upgrades/downgrades.</div>'

    upside_pct = ((d['target_mean'] - price) / price * 100) if d['target_mean'] and price else 0
    iv_1sig = round(price * d['near_iv'] * math.sqrt(30/365), 2) if d['near_iv'] and price else 0
    nt_low  = round(price - iv_1sig, 2)
    nt_high = round(price + iv_1sig, 2)

    # TA summary
    above_ema20  = d['ema20']  and price and price > d['ema20']
    above_ema200 = d['ema200'] and price and price > d['ema200']
    ta_ma_str = ('Above EMA20 / Above EMA200' if above_ema20 and above_ema200 else
                 'Below EMA20 / Above EMA200' if not above_ema20 and above_ema200 else
                 'Above EMA20 / Below EMA200' if above_ema20 and not above_ema200 else
                 'Below EMA20 / Below EMA200')
    macd_str = 'Positive' if d['macdh'] and d['macdh'] > 0 else 'Negative'

    ta_overall = ('BULLISH' if (above_ema200 and d['macdh'] and d['macdh'] > 0
                                and d['rsi'] and 45 <= d['rsi'] <= 70) else
                  'BEARISH' if (not above_ema200 or (d['macdh'] and d['macdh'] < 0)) else 'NEUTRAL')
    ta_cls = ('bull-color' if ta_overall == 'BULLISH' else
              'bear-color' if ta_overall == 'BEARISH' else 'neutral-color')

    date_fmt = datetime.datetime.strptime(report_date, '%Y-%m-%d').strftime('%B %d, %Y')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${ticker} — Research Desk</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f0; color: #1a1a1a; padding: 2rem; min-height: 100vh; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  .header {{ display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 1.5rem; padding-bottom: 1rem; border-bottom: 1px solid #e0e0d8; flex-wrap: wrap; gap: 16px; }}
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
  .bull-color    {{ color: #2d7a2d; }}
  .bear-color    {{ color: #a32d2d; }}
  .neutral-color {{ color: #888; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-bottom: 1.25rem; }}
  .card {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 10px; padding: 0.65rem 0.875rem; }}
  .card-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 3px; }}
  .card-val {{ font-size: 14px; font-weight: 700; }}
  .card-sub {{ font-size: 10px; color: #888; margin-top: 2px; line-height: 1.3; }}
  .divider {{ height: 1px; background: #e8e8e0; margin: 1.25rem 0; }}
  .section-lbl {{ font-size: 9px; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 10px; }}
  .analyst-wrap {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; }}
  .analyst-top {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; flex-wrap: wrap; gap: 8px; }}
  .analyst-count {{ font-size: 11px; color: #666; }}
  .analyst-count strong {{ color: #1a1a1a; font-size: 13px; }}
  .analyst-targets {{ display: flex; gap: 20px; }}
  .at-item {{ text-align: right; }}
  .at-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; }}
  .at-val {{ font-size: 14px; font-weight: 700; }}
  .analyst-bar-row {{ display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin-bottom: 8px; gap: 1px; }}
  .ab-sb {{ background: #1a5c1a; }} .ab-b {{ background: #2d7a2d; }}
  .ab-h  {{ background: #e8e8e0; }} .ab-s {{ background: #a32d2d; }} .ab-ss {{ background: #6b0000; }}
  .analyst-legend {{ display: flex; gap: 14px; flex-wrap: wrap; }}
  .al-item {{ display: flex; align-items: center; gap: 4px; font-size: 10px; color: #555; }}
  .al-dot {{ width: 8px; height: 8px; border-radius: 2px; }}
  .analyst-recent {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid #f0f0ea; }}
  .ar-lbl {{ font-size: 9px; color: #999; text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 6px; }}
  .ar-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 5px; }}
  .ar-item {{ background: #f8f8f5; border: 1px solid #e8e8e0; border-radius: 6px; padding: 4px 7px; }}
  .ar-firm {{ font-size: 9px; font-weight: 600; color: #555; margin-bottom: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .ar-action {{ display: flex; justify-content: space-between; align-items: center; }}
  .ar-grade {{ font-size: 9px; color: #888; }}
  .ar-target {{ font-size: 10px; font-weight: 700; }}
  .ar-change {{ font-size: 8px; color: #999; }}
  .horizon-wrap {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; margin-bottom: 1.25rem; }}
  .hz-card {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 12px; padding: 1rem 1.125rem; }}
  .hz-label {{ font-size: 9px; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }}
  .hz-range {{ font-size: 20px; font-weight: 700; color: #1a1a1a; margin-bottom: 3px; }}
  .hz-bullets {{ list-style: none; }}
  .hz-bullets li {{ font-size: 10px; color: #555; padding: 2px 0; border-bottom: 1px solid #f5f5f0; display: flex; justify-content: space-between; }}
  .hz-bullets li:last-child {{ border-bottom: none; }}
  .hz-val {{ font-weight: 600; color: #1a1a1a; }}
  .sent-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin-bottom: 1.25rem; }}
  .sent-card {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 10px; padding: 9px 11px; }}
  .sent-src {{ font-size: 9px; color: #999; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.04em; }}
  .sent-val {{ font-size: 15px; font-weight: 700; }}
  .sent-bar-wrap {{ height: 4px; background: #f0f0ea; border-radius: 2px; margin: 5px 0 3px; overflow: hidden; }}
  .sent-bar {{ height: 100%; border-radius: 2px; }}
  .sent-pct {{ font-size: 9px; color: #888; }}
  .pm-list {{ margin-bottom: 1.25rem; }}
  .pm-row {{ display: flex; align-items: center; padding: 7px 0; border-bottom: 1px solid #e8e8e0; gap: 10px; }}
  .pm-row:last-child {{ border-bottom: none; }}
  .pm-q {{ font-size: 12px; color: #1a1a1a; flex: 1; }}
  .pm-bar-outer {{ width: 80px; height: 4px; background: #f0f0ea; border-radius: 2px; flex-shrink: 0; overflow: hidden; }}
  .pm-bar-inner {{ height: 100%; border-radius: 2px; }}
  .pm-prob {{ font-size: 12px; font-weight: 600; white-space: nowrap; min-width: 48px; text-align: right; }}
  .pm-liq {{ font-size: 10px; color: #999; min-width: 60px; text-align: right; }}
  .scenarios {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 1.25rem; }}
  @media(max-width:560px){{ .scenarios {{ grid-template-columns: 1fr; }} }}
  .sc {{ background: #fff; border: 1px solid #e8e8e0; border-radius: 12px; padding: 0.875rem 1rem; }}
  .sc-bull {{ border-top: 3px solid #2d7a2d; }}
  .sc-bear {{ border-top: 3px solid #a32d2d; }}
  .sc-pct {{ font-size: 26px; font-weight: 800; margin-bottom: 4px; }}
  .sc-label {{ font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #999; margin-bottom: 6px; }}
  .sc-trigger {{ font-size: 11px; color: #444; margin-bottom: 4px; line-height: 1.5; }}
  .sc-target {{ font-size: 11px; font-weight: 600; margin-bottom: 8px; }}
  .bar-wrap {{ height: 5px; background: #f0f0ea; border-radius: 3px; margin-bottom: 8px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 3px; }}
  .bar-bull {{ background: #2d7a2d; }}
  .bar-bear {{ background: #a32d2d; }}
  .tags {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .tag {{ font-size: 9px; font-weight: 600; padding: 2px 7px; border-radius: 10px; background: #f0f0ea; color: #555; }}
  .quotes {{ margin-bottom: 1.25rem; }}
  .q-row {{ display: flex; align-items: flex-start; gap: 10px; padding: 8px 0; border-bottom: 1px solid #e8e8e0; }}
  .q-row:last-child {{ border-bottom: none; }}
  .q-tag {{ font-size: 9px; font-weight: 700; padding: 2px 7px; border-radius: 10px; background: #f0f0ea; color: #555; white-space: nowrap; margin-top: 2px; }}
  .q-text {{ font-size: 11px; color: #333; line-height: 1.5; }}
  .q-eng {{ font-size: 9px; color: #999; margin-top: 2px; }}
  .bottom-line {{ background: #fff; border: 1.5px solid #c8c8c0; border-radius: 14px; padding: 1rem 1.375rem; margin-bottom: 1.25rem; }}
  .bl-lbl {{ font-size: 9px; font-weight: 700; color: #999; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }}
  .bl-top {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }}
  .bl-badge {{ font-size: 10px; font-weight: 700; padding: 2px 10px; border-radius: 10px; }}
  .bl-text {{ font-size: 12px; color: #333; line-height: 1.75; }}
  .disclaimer {{ font-size: 9px; color: #bbb; text-align: center; margin-top: 1.5rem; }}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="header-left">
      <div class="ticker">${ticker} <span class="company">{company}</span></div>
      <div class="meta">{date_fmt} &nbsp;·&nbsp; {d['exchange']} &nbsp;·&nbsp; Educational use only — not financial advice</div>
    </div>
    <div class="scores-wrap">
      <div class="scores-row">
        <div class="score-block">
          <div class="score-ring">
            <svg width="64" height="64" viewBox="0 0 64 64">
              <circle cx="32" cy="32" r="26" fill="none" stroke="#f0f0ea" stroke-width="5"/>
              <circle cx="32" cy="32" r="26" fill="none" stroke="{tape_color}" stroke-width="5"
                stroke-dasharray="163.4" stroke-dashoffset="{tape_off}"
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
              <circle cx="32" cy="32" r="26" fill="none" stroke="{thesis_color}" stroke-width="5"
                stroke-dasharray="163.4" stroke-dashoffset="{thesis_off}"
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
      <div style="display:flex;justify-content:flex-end;">
        <div class="divergence-badge {div_cls}">{div_txt}</div>
      </div>
    </div>
  </div>

  <div class="cards">
    <div class="card">
      <div class="card-lbl">Price</div>
      <div class="card-val">{dollars(price)}</div>
      <div class="card-sub">52W {dollars(d['w52_low'])} – {dollars(d['w52_high'])}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Weekly TA</div>
      <div class="card-val {ta_cls}">{ta_overall}</div>
      <div class="card-sub">{ta_ma_str} · MACD {macd_str}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Analyst Rating</div>
      <div class="card-val {'bull-color' if rec_mean_label(d['rec_mean']) in ('Strong Buy','Buy') else 'neutral-color'}">{rec_mean_label(d['rec_mean'])}</div>
      <div class="card-sub">{d['num_analysts']} analysts · mean {d['rec_mean']:.2f}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Call Wall</div>
      <div class="card-val">{dollars(d['call_wall']) if d['call_wall'] else '—'}</div>
      <div class="card-sub">OI {d['call_wall_oi']:,}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Put Wall</div>
      <div class="card-val">{dollars(d['put_wall']) if d['put_wall'] else '—'}</div>
      <div class="card-sub">OI {d['put_wall_oi']:,}</div>
    </div>
    <div class="card">
      <div class="card-lbl">Next Earnings</div>
      <div class="card-val">{d['earnings_str']}</div>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Analyst Coverage — {d['num_analysts']} firms</div>
  <div class="analyst-wrap">
    <div class="analyst-top">
      <div>
        <div class="analyst-count"><strong>{rc['strongBuy'] + rc['buy']} of {sum(rc.values())}</strong> analysts bullish</div>
      </div>
      <div class="analyst-targets">
        <div class="at-item"><div class="at-lbl">Low</div><div class="at-val bear-color">{dollars(d['target_low'])}</div></div>
        <div class="at-item"><div class="at-lbl">Mean</div><div class="at-val">{dollars(d['target_mean'])}</div></div>
        <div class="at-item"><div class="at-lbl">High</div><div class="at-val bull-color">{dollars(d['target_high'])}</div></div>
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
      <div class="al-item"><div class="al-dot" style="background:#1a5c1a"></div> Strong Buy {rc['strongBuy']}</div>
      <div class="al-item"><div class="al-dot" style="background:#2d7a2d"></div> Buy {rc['buy']}</div>
      <div class="al-item"><div class="al-dot" style="background:#e8e8e0"></div> Hold {rc['hold']}</div>
      <div class="al-item"><div class="al-dot" style="background:#a32d2d"></div> Sell {rc['sell']}</div>
      <div class="al-item"><div class="al-dot" style="background:#6b0000"></div> Strong Sell {rc['strongSell']}</div>
    </div>
    <div class="analyst-recent">
      <div class="ar-lbl">Recent upgrades / downgrades</div>
      <div class="ar-grid">{ud_html}</div>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Price Target Horizons</div>
  <div class="horizon-wrap">
    <div class="hz-card">
      <div class="hz-label">Near-Term (30-day IV range)</div>
      <div class="hz-range">{dollars(nt_low)} – {dollars(nt_high)}</div>
      <ul class="hz-bullets">
        <li><span>1σ IV move</span><span class="hz-val">±{dollars(iv_1sig)}</span></li>
        <li><span>Call wall</span><span class="hz-val">{dollars(d['call_wall'])}</span></li>
        <li><span>Put wall</span><span class="hz-val">{dollars(d['put_wall'])}</span></li>
        <li><span>IV (near)</span><span class="hz-val">{round(d['near_iv']*100)}%</span></li>
      </ul>
    </div>
    <div class="hz-card">
      <div class="hz-label">Analyst Consensus Target</div>
      <div class="hz-range">{dollars(d['target_mean'])}</div>
      <ul class="hz-bullets">
        <li><span>Upside from current</span><span class="hz-val">{pct_fmt(upside_pct)}</span></li>
        <li><span>Bear target</span><span class="hz-val">{dollars(d['target_low'])}</span></li>
        <li><span>Bull target</span><span class="hz-val">{dollars(d['target_high'])}</span></li>
        <li><span>Analysts</span><span class="hz-val">{d['num_analysts']}</span></li>
      </ul>
    </div>
    <div class="hz-card">
      <div class="hz-label">LEAP Options (180d+)</div>
      <div class="hz-range">{dollars(d['leap_strike'])}</div>
      <ul class="hz-bullets">
        <li><span>Top OTM strike</span><span class="hz-val">{dollars(d['leap_strike'])}</span></li>
        <li><span>Open interest</span><span class="hz-val">{d['leap_oi']:,}</span></li>
      </ul>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Scenarios</div>
  <div class="scenarios">{scenarios_html}</div>

  <div class="divider"></div>
  <div class="section-lbl">Social Sentiment</div>
  <div class="sent-grid">
    <div class="sent-card">
      <div class="sent-src">X / Twitter</div>
      <div class="sent-val {'bull-color' if x_bull > 55 else ('bear-color' if x_bull < 45 else 'neutral-color')}">{round(x_bull)}% Bull</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{round(x_bull)}%;background:{'#2d7a2d' if x_bull>55 else ('#a32d2d' if x_bull<45 else '#888')}"></div></div>
    </div>
    <div class="sent-card">
      <div class="sent-src">Reddit</div>
      <div class="sent-val {'bull-color' if reddit_bull > 55 else ('bear-color' if reddit_bull < 45 else 'neutral-color')}">{round(reddit_bull)}% Bull</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{round(reddit_bull)}%;background:{'#2d7a2d' if reddit_bull>55 else ('#a32d2d' if reddit_bull<45 else '#888')}"></div></div>
    </div>
    <div class="sent-card">
      <div class="sent-src">News</div>
      <div class="sent-val {'bull-color' if news_sent > 0.1 else ('bear-color' if news_sent < -0.1 else 'neutral-color')}">{news_bull}% Pos</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{news_bull}%;background:{'#2d7a2d' if news_sent>0.1 else ('#a32d2d' if news_sent<-0.1 else '#888')}"></div></div>
    </div>
    <div class="sent-card">
      <div class="sent-src">StockTwits</div>
      <div class="sent-val {'bull-color' if st_bull_pct > 55 else ('bear-color' if st_bull_pct < 45 else 'neutral-color')}">{st_bull_pct}% Bull</div>
      <div class="sent-bar-wrap"><div class="sent-bar" style="width:{st_bull_pct}%;background:{'#2d7a2d' if st_bull_pct>55 else ('#a32d2d' if st_bull_pct<45 else '#888')}"></div></div>
      <div class="sent-pct">{st['bull']}B / {st['bear']}Be of {st['total']} msgs</div>
    </div>
  </div>

  <div class="divider"></div>
  <div class="section-lbl">Quotes &amp; Headlines</div>
  <div class="quotes">{quotes_html}</div>

  <div class="divider"></div>
  <div class="section-lbl">Polymarket</div>
  <div class="pm-list">{poly_rows}</div>

  <div class="divider"></div>
  <div class="bottom-line">
    <div class="bl-lbl">Bottom Line — AI-generated summary</div>
    <div class="bl-top">
      <span class="bl-badge" style="background:{tape_badge_bg};color:{tape_badge_color}">Tape: {tape_lbl}</span>
      <span class="bl-badge" style="background:{thesis_badge_bg};color:{thesis_badge_color}">Thesis: {thesis_lbl}</span>
    </div>
    <div class="bl-text">{bottom_line}</div>
  </div>

  <div class="disclaimer">Educational use only. Not financial advice. Data sourced from public APIs. Scores are algorithmic estimates, not recommendations.</div>
</div>
</body>
</html>"""

# ── Manifest update ────────────────────────────────────────────────────────────

def update_manifest(ticker, company, report_date, filename, tape, thesis):
    manifest_path = os.path.join(OUT_DIR, 'manifest.json')
    manifest = {'reports': []}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            try: manifest = json.load(f)
            except Exception: pass

    reports = manifest.get('reports', [])
    reports = [r for r in reports if not (r['ticker'] == ticker and r['date'] == report_date)]
    reports.insert(0, {
        'ticker': ticker,
        'company': company,
        'date': report_date,
        'file': filename,
        'tape': tape,
        'tapeLabel': score_label(tape).title(),
        'thesis': thesis,
        'thesisLabel': score_label(thesis).title(),
    })
    manifest['reports'] = reports
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: research_desk.py TICKER'); sys.exit(1)
    ticker = sys.argv[1].strip().upper()
    report_date = datetime.date.today().isoformat()

    print(f'Collecting data for {ticker}…')
    d = collect(ticker)
    print(f'  price=${d["price"]:.2f}, company={d["company"]}')

    print('Computing scores…')
    tape, thesis = compute_scores(d)
    print(f'  Tape={tape}, Thesis={thesis}')

    print('Generating bottom line…')
    bottom_line = gemini_bottom_line(d, tape, thesis)

    print('Generating HTML…')
    html = generate_html(d, tape, thesis, bottom_line, report_date)

    filename = f'{ticker}_{report_date}.html'
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, filename)
    with open(out_path, 'w') as f:
        f.write(html)
    print(f'  → {out_path}')

    print('Updating manifest…')
    update_manifest(ticker, d['company'], report_date, filename, tape, thesis)

    print(f'\n✅ Report: {filename}  Tape={tape}  Thesis={thesis}')

if __name__ == '__main__':
    main()
