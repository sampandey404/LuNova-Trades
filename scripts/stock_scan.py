#!/usr/bin/env python3
"""
Stock Picks scan — Finviz universe → yfinance weekly TA → 6 hard filters → 0-13 score.
Output: public/tools/stock-picks/results.json
"""
import json, os, datetime, time, requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from finviz.screener import Screener

OUT = os.path.join(os.path.dirname(__file__), '..', 'public', 'tools', 'stock-picks', 'results.json')
ADANOS_KEY = os.environ.get('ADANOS_API_KEY', '')
ADANOS_BASE = 'https://api.adanos.org'

FILTERS = [
    'cap_largeover',
    'sh_avgvol_o2000',
    'ta_highlow52w_b40h',
    'ta_perf_4wup',
    'ta_perf2_1wup',
    'an_recom_buybetter',
]

def get_universe():
    stock_list = Screener(filters=FILTERS, order='-marketcap', rows=100)
    return [s['Ticker'] for s in stock_list]

def get_adanos(ticker):
    if not ADANOS_KEY:
        return {}
    try:
        headers = {'X-API-Key': ADANOS_KEY}
        x_res = requests.get(f'{ADANOS_BASE}/x/stocks/v1/stock/{ticker}', headers=headers, timeout=10)
        news_res = requests.get(f'{ADANOS_BASE}/news/stocks/v1/stock/{ticker}', headers=headers, timeout=10)
        x = x_res.json() if x_res.ok else {}
        news = news_res.json() if news_res.ok else {}
        return {'x': x, 'news': news}
    except Exception:
        return {}

def analyze(ticker):
    tk = yf.Ticker(ticker)

    hist = tk.history(period='2y', interval='1wk')
    if hist.empty or len(hist) < 30:
        return None

    hist.ta.rsi(length=14, append=True)
    hist.ta.macd(append=True)
    hist.ta.adx(length=14, append=True)
    hist.ta.ema(length=20, append=True)
    hist.ta.ema(length=200, append=True)
    hist.ta.stoch(append=True)

    row = hist.iloc[-1]
    prev = hist.iloc[-2]

    close = float(row['Close'])
    high  = float(row['High'])
    low   = float(row['Low'])
    open_ = float(row['Open'])

    rsi    = row.get('RSI_14')
    macdh  = row.get('MACDh_12_26_9')
    adx    = row.get('ADX_14')
    ema20  = row.get('EMA_20')
    ema200 = row.get('EMA_200')
    stochk = row.get('STOCHk_14_3_3')
    stochd = row.get('STOCHd_14_3_3')
    prev_rsi = prev.get('RSI_14')

    if any(v is None or (hasattr(v, '__float__') and pd.isna(float(v)))
           for v in [rsi, macdh, adx, ema20, ema200, stochk, stochd]):
        return None

    rsi = float(rsi); macdh = float(macdh); adx = float(adx)
    ema20 = float(ema20); ema200 = float(ema200)
    stochk = float(stochk); stochd = float(stochd)
    prev_rsi = float(prev_rsi) if prev_rsi is not None else rsi

    try:
        ath = float(tk.fast_info.fifty_two_week_high)
        pct_below_ath = round((ath - close) / ath * 100, 1) if ath else 0
    except Exception:
        pct_below_ath = 0

    candle_range = high - low
    lower_wick = (min(open_, close) - low) if candle_range > 0 else 0
    lower_wick_pct = round(lower_wick / candle_range * 100, 1) if candle_range > 0 else 0

    # 6 Hard Filters
    if not (15 <= pct_below_ath <= 60): return None
    if close <= ema200: return None
    if macdh <= 0: return None
    if adx >= 32: return None
    if not (28 <= rsi <= 54): return None
    if lower_wick_pct < 25: return None

    # Technical Score 0-10
    tech = 0
    signals = []
    if stochk > stochd:      tech += 2; signals.append('Stoch bullish cross')
    if rsi > prev_rsi:       tech += 1; signals.append('RSI rising')
    if close > ema20:        tech += 2; signals.append('Above EMA20')
    if adx < 20:             tech += 1; signals.append('ADX < 20 (low trend noise)')
    if lower_wick_pct >= 50: tech += 1; signals.append('Strong lower wick')
    if macdh > 0:            tech += 2; signals.append('MACD hist positive')
    if adx < 25:             tech += 1; signals.append('ADX < 25')

    # Stage
    if rsi < 38 and close < ema20:
        stage = 1
    elif 38 <= rsi <= 50 and macdh > 0:
        stage = 2
    elif rsi > 50 and close > ema20 and stochk > stochd:
        stage = 3
    else:
        stage = 2

    if tech < 3:
        return None

    # Social sentiment (Adanos)
    social_score = 0
    social = {'x': {}, 'news': {}}
    if tech >= 3:
        data = get_adanos(ticker)
        x_data = data.get('x', {})
        news_data = data.get('news', {})
        bullish_pct = x_data.get('bullish_pct', 0) or 0
        news_sentiment = news_data.get('sentiment_score', 0) or 0
        x_trend = x_data.get('trend', '')
        news_trend = news_data.get('trend', '')

        if bullish_pct > 50:     social_score += 1
        if news_sentiment > 0.2: social_score += 1
        if x_trend == 'rising' or news_trend == 'rising': social_score += 1

        social = {
            'x': {'bullish_pct': bullish_pct, 'trend': x_trend},
            'news': {'sentiment_score': round(news_sentiment, 2), 'trend': news_trend},
        }

    total = tech + social_score

    try:
        info = tk.info
        company = info.get('longName', ticker)
    except Exception:
        company = ticker

    score_class = 'high' if total >= 9 else ('medium' if total >= 5 else 'low')

    return {
        'ticker': ticker,
        'company': company,
        'score': total,
        'maxScore': 13,
        'scoreClass': score_class,
        'stage': stage,
        'drawdown': pct_below_ath,
        'rsi': round(rsi, 1),
        'adx': round(adx, 1),
        'macd': round(macdh, 3),
        'signals': signals,
        'thesis': f"Stage {stage} setup. RSI {round(rsi,1)}, ADX {round(adx,1)}, {round(pct_below_ath,1)}% off 52W high.",
        'social': social,
        'tech': tech,
    }

def main():
    scan_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M ET')
    print('Fetching Finviz universe…')
    try:
        tickers = get_universe()
    except Exception as e:
        print(f'Finviz error: {e}')
        tickers = []
    print(f'{len(tickers)} tickers from Finviz')

    active, on_deck, monitor = [], [], []
    for ticker in tickers:
        print(f'  {ticker}…', end=' ', flush=True)
        try:
            result = analyze(ticker)
            if result is None:
                print('filtered')
                continue
            tech = result.pop('tech')
            if tech >= 5:
                active.append(result)
                print(f'ACTIVE score={result["score"]}')
            elif tech >= 3:
                monitor.append(result)
                print(f'MONITOR score={result["score"]}')
            else:
                print('discard')
        except Exception as e:
            print(f'ERROR: {e}')
        time.sleep(0.3)

    active.sort(key=lambda x: -x['score'])
    on_deck.sort(key=lambda x: -x['score'])
    monitor.sort(key=lambda x: -x['score'])

    out = {
        'scanDate': scan_date,
        'universe': len(tickers),
        'active': active,
        'onDeck': on_deck,
        'monitor': monitor,
    }
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'\n✅ {len(active)} active, {len(monitor)} monitor → {os.path.abspath(OUT)}')

if __name__ == '__main__':
    main()
