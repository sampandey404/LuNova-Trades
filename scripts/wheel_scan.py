#!/usr/bin/env python3
"""
Wheel Tracker scan — fetches live option chains via yfinance, scores puts by delta.
Output: public/tools/wheel-tracker/wheel-results.json
"""
import json, math, os, sys, datetime
from scipy.stats import norm
import yfinance as yf

OUT = os.path.join(os.path.dirname(__file__), '..', 'public', 'tools', 'wheel-tracker', 'wheel-results.json')

TICKERS = {
    'QQQ':  {'mode': 'multi'},
    'SPY':  {'mode': 'single'},
    'MSTR': {'mode': 'single'},
    'NVDA': {'mode': 'single'},
    'META': {'mode': 'single'},
    'AAPL': {'mode': 'single'},
    'MSFT': {'mode': 'single'},
    'AMZN': {'mode': 'single'},
    'GOOGL': {'mode': 'single'},
}

DEFAULT_IV = {'QQQ':0.22,'SPY':0.20,'MSTR':0.90,'NVDA':0.50,'META':0.40,
              'AAPL':0.30,'MSFT':0.28,'AMZN':0.38,'GOOGL':0.32}

def bs_put_delta(S, K, iv, T, r=0.05):
    if T <= 0 or iv <= 0: return -0.5
    d1 = (math.log(S/K) + (r + 0.5*iv**2)*T) / (iv*math.sqrt(T))
    return norm.cdf(d1) - 1  # negative; we use abs() for filtering

def next_weekly_expiry(dates):
    """Pick the nearest Friday expiry that is at least 2 days away."""
    today = datetime.date.today()
    min_date = today + datetime.timedelta(days=2)
    candidates = []
    for d in dates:
        try:
            dt = datetime.date.fromisoformat(d)
        except Exception:
            continue
        if dt >= min_date and dt.weekday() in (0, 2, 4):  # Mon/Wed/Fri
            candidates.append(dt)
    if candidates:
        return str(sorted(candidates)[0])
    # Fallback: any nearest date ≥ min_date
    all_dates = sorted(datetime.date.fromisoformat(d) for d in dates
                       if datetime.date.fromisoformat(d) >= min_date)
    return str(all_dates[0]) if all_dates else str(sorted(dates)[-1])

def fetch_puts(ticker):
    tk = yf.Ticker(ticker)

    # Spot price
    try:
        spot = tk.fast_info.last_price
    except Exception:
        spot = None
    if not spot:
        info = tk.info
        for f in ('currentPrice','regularMarketPrice','previousClose'):
            if info.get(f):
                spot = float(info[f]); break
    if not spot:
        raise ValueError(f'{ticker}: could not get spot price')

    # Expiry dates
    dates = tk.options
    if not dates:
        raise ValueError(f'{ticker}: no option expiries found')
    expiry = next_weekly_expiry(list(dates))
    dte = max(1, (datetime.date.fromisoformat(expiry) - datetime.date.today()).days)
    T = dte / 365.0

    # Put chain
    chain = tk.option_chain(expiry)
    puts = chain.puts
    if puts is None or puts.empty:
        raise ValueError(f'{ticker}: empty put chain for {expiry}')

    results = []
    for _, row in puts.iterrows():
        strike = float(row.get('strike', 0) or 0)
        bid    = float(row.get('bid', 0) or 0)
        last   = float(row.get('lastPrice', 0) or 0)
        prem   = bid if bid > 0 else last
        raw_iv = float(row.get('impliedVolatility', 0) or 0)
        iv     = raw_iv if raw_iv >= 0.05 else DEFAULT_IV.get(ticker, 0.25)

        if strike >= spot: continue          # must be OTM
        if prem < 0.50: continue             # min $0.50 premium

        delta = bs_put_delta(spot, strike, iv, T)
        abs_delta = abs(delta)
        if abs_delta < 0.10 or abs_delta > 0.35: continue

        results.append({
            'ticker': ticker,
            'spot':   round(spot, 2),
            'type':   'CSP',
            'strike': round(strike, 2),
            'expiry': expiry,
            'dte':    dte,
            'prem':   round(prem, 2),
            'delta':  round(abs_delta, 3),
            'iv':     f'{round(iv*100)}%',
            'be':     round(strike - prem, 2),
            'prob':   round((1 - abs_delta) * 100),
        })

    return results, spot

def pick_qqq(puts):
    picks = []
    target = sorted([p for p in puts if 0.17 <= p['delta'] <= 0.23],
                    key=lambda p: abs(p['delta'] - 0.20))
    if target: picks.append({**target[0], 'band': 'Target Δ0.20'})

    high = sorted([p for p in puts if 0.23 < p['delta'] <= 0.30],
                  key=lambda p: -p['prem'])
    if high: picks.append({**high[0], 'band': 'Higher prem'})

    safe = sorted([p for p in puts if 0.13 <= p['delta'] < 0.17],
                  key=lambda p: abs(p['delta'] - 0.15))
    if safe: picks.append({**safe[0], 'band': 'Safer'})
    return picks

def pick_single(puts, ticker):
    candidates = sorted([p for p in puts if 0.15 <= p['delta'] <= 0.26],
                        key=lambda p: abs(p['delta'] - 0.20))
    if candidates:
        return [{**candidates[0], 'band': 'CSP'}]
    return []

def main():
    now_et = datetime.datetime.now().strftime('%Y-%m-%d %H:%M ET')
    picks  = []
    errors = []

    for ticker, cfg in TICKERS.items():
        print(f'{ticker}: ', end='', flush=True)
        try:
            puts, spot = fetch_puts(ticker)
            print(f'spot=${spot:.2f}  {len(puts)} qualifying puts', flush=True)
            if cfg['mode'] == 'multi':
                p = pick_qqq(puts)
            else:
                p = pick_single(puts, ticker)
            picks.extend(p)
            print(f'  → {len(p)} pick(s)')
        except Exception as e:
            msg = str(e)
            print(f'ERROR: {msg}')
            errors.append({'ticker': ticker, 'error': msg})

    out = {'scanTime': now_et, 'picks': picks, 'errors': errors}
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'\n✅ {len(picks)} picks → {os.path.abspath(OUT)}')
    if errors:
        print(f'⚠️  {len(errors)} errors: {[e["ticker"] for e in errors]}')

if __name__ == '__main__':
    main()
