# LuNova Trades — Claude Resume File
# READ THIS FIRST if resuming after a rate limit

## Repo
- GitHub: `https://github.com/sampandey404/LuNova-Trades`
- Local: `~/LuNova-Trades/`
- Live site: `https://sampandey404.github.io/LuNova-Trades/`
- PAT token: `GH_PAT_REDACTED`

---

## Current Build Status

### ✅ Done (v1 — live but broken)
- Vite React nav shell with 3 iframe tabs — working, deployed
- Wheel Tracker HTML copied and cowork calls removed — BROKEN (corsproxy approach failed)
- Stock Picks HTML refactored to load results.json — BROKEN (wrong scan logic)
- Research Desk seed reports + index page — static only, not live
- GitHub Actions: deploy.yml working ✅
- GitHub Secrets: ADANOS_API_KEY added ✅

### 🔲 In Progress (v2 rebuild — NOT YET STARTED)
Everything below needs to be built from scratch. Do NOT edit v1 files without reading this first.

---

## V2 Architecture (approved by user)

**GitHub Actions as compute engine.** Browser triggers workflow via GitHub API → Python/yfinance runs → commits JSON/HTML → browser polls and renders. ~60-90s wait is acceptable.

**PAT token embedded in browser JS** — user approved this for personal use.

**No schedules** — everything is on-demand via "Run Scan" / "Run New Report" buttons.

---

## V2 Build Checklist (do these IN ORDER)

### Step 1 — Add GEMINI_API_KEY secret to GitHub
```
Key: GEMINI_API_KEY
Value: AIzaSyAdZMsaCeX0eJCuIdKN7TcngxglAcSkjss
```
Use the GitHub API with the PAT to add it (same pattern as ADANOS_API_KEY was added).

### Step 2 — Create `scripts/wheel_scan.py`
- Tickers: QQQ (3 picks), SPY, MSTR, NVDA, META, AAPL, MSFT, AMZN, GOOGL (1 each)
- yfinance: `fast_info` → spot price, `.options` → expiries, `.option_chain(expiry).puts` → chain
- Filter: OTM (strike < spot), premium ≥ $0.50, delta 0.10–0.35
- Black-Scholes put delta: `bsPutDelta(S, K, iv, T, r=0.05)` using scipy.stats.norm
- QQQ: 3 picks (target Δ0.20, higher prem Δ0.23-0.30, safer Δ0.13-0.17)
- Others: 1 pick closest to Δ0.20
- Output: `public/tools/wheel-tracker/wheel-results.json`
  ```json
  {"scanTime": "...", "picks": [{ticker, band, strike, expiry, dte, prem, delta, iv, be, prob, spot}], "errors": []}
  ```

### Step 3 — Create `.github/workflows/wheel-scan.yml`
- workflow_dispatch, no inputs
- pip install yfinance scipy
- python scripts/wheel_scan.py
- git commit + push wheel-results.json

### Step 4 — Rewrite `public/tools/wheel-tracker.html` scan trigger
- Remove ALL fetch() / corsproxy calls (lines with YF_PROXY, query1.finance, query2.finance)
- Replace `runScan()` function: trigger wheel-scan.yml → poll → fetch wheel-results.json → call existing `renderRankedResults()`
- Polling pattern: POST dispatch → sleep 8s → GET /actions/runs every 5s → on success fetch JSON
- On failure: show "Scan failed — [View error log →]" with run.html_url
- Spinner text updates: "Triggering scan…" → "Fetching options data… (~60s)" → "Processing…"

### Step 5 — Create `scripts/stock_scan.py`
Finviz filters (static, translated from user's URL):
```python
filters = ['cap_largeover', 'sh_avgvol_o2000', 'ta_highlow52w_b40h',
           'ta_perf_4wup', 'ta_perf2_1wup', 'an_recom_buybetter']
order = '-marketcap'
```
For each ticker:
- `yf.Ticker(t).history(period='2y', interval='1wk')` → weekly OHLCV
- pandas-ta: `.ta.rsi(14)`, `.ta.macd()`, `.ta.adx(14)`, `.ta.ema(20)`, `.ta.ema(200)`, `.ta.stoch()`
- `fast_info.fifty_two_week_high` → pct_below_ath
- lower_wick_pct from candle OHLC

6 Hard Filters:
1. 15 ≤ pct_below_ath ≤ 60
2. close > EMA_200
3. MACDh_12_26_9 > 0
4. ADX_14 < 32
5. 28 ≤ RSI_14 ≤ 54
6. lower_wick_pct ≥ 25

Scoring (0-10 tech + 0-3 social = max 13):
- STOCHk > STOCHd: +2
- RSI rising week-over-week: +1
- close > EMA_20: +2
- ADX < 20: +1
- lower_wick_pct ≥ 50: +1
- MACDh > 0: +2
- ADX < 25: +1

Social (Adanos, only for tech ≥ 3):
- X bullish_pct > 50: +1
- News sentiment_score > 0.2: +1
- X or News trend == "rising": +1

Stage: 1=RSI<38 & close<EMA20, 2=RSI 38-50 & MACDh>0, 3=RSI>50 & close>EMA20 & STOCHk>STOCHd
Buckets: Active=tech≥5, Monitor=tech 3-4, Discard=tech<3

Output: `public/tools/stock-picks/results.json`
```json
{"scanDate": "...", "universe": N, "active": [...], "onDeck": [...], "monitor": [...]}
```
Each card: {ticker, company, score, maxScore:13, scoreClass, stage, drawdown, rsi, adx, macd, signals:[], thesis, social:{reddit,x,news}}

### Step 6 — Create `.github/workflows/stock-scan.yml`
- workflow_dispatch, no inputs
- pip install yfinance pandas pandas-ta finviz requests
- python scripts/stock_scan.py (ADANOS_API_KEY from secret)
- git commit + push results.json

### Step 7 — Update `public/tools/stock-picks/index.html`
- Add "Run Scan" button to header area (keep existing CSS/card layout exactly)
- Button triggers stock-scan.yml → polls → re-fetches results.json → re-renders cards
- Show scan timestamp from results.json.scanDate

### Step 8 — Create `scripts/research_desk.py`
Input: sys.argv[1] = ticker

Data via yfinance:
- `tk.info` → price, 52W high/low, targetMeanPrice/High/Low, recommendationMean, numberOfAnalystOpinions, earningsDate
- `tk.recommendations` → strongBuy/buy/hold/sell/strongSell
- `tk.upgrades_downgrades` → last 8 firm actions
- `tk.history(period='1y', interval='1wk')` + pandas-ta → RSI, MACD, ADX, EMA20/200
- `tk.options` → expiry list; `tk.option_chain(near_exp)` → call/put walls (highest OI OTM)
- LEAP: first expiry > 180 days → call chain → top OI OTM strike

Data via Adanos (ADANOS_API_KEY env):
- reddit, x, news, polymarket endpoints

Data via StockTwits:
- `GET https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json`
- Count Bullish vs Bearish entities

Tape Score (0-100): Technical 35% + Options walls 30% + Sentiment 25% + Polymarket 10%
Thesis Score (0-100): Analyst consensus 40% + Analyst upside 25% + LEAP OI 20% + Polymarket 15%
(Full formulas in SYSTEM_INSTRUCTIONS_v2.md at `/Users/s.r.p/Documents/Claude/Projects/Quant Research Desk/SYSTEM_INSTRUCTIONS_v2.md`)

Gemini call (1 per report):
```python
import google.generativeai as genai
genai.configure(api_key=os.environ['GEMINI_API_KEY'])
model = genai.GenerativeModel('gemini-1.5-flash')
# Prompt: 3-sentence bottom line with tape/thesis scores, key levels, no financial advice
bottom_line = model.generate_content(prompt).text
```

Outputs:
1. `public/tools/research-desk/TICKER_YYYY-MM-DD.html` (same CSS template as existing HOOD/NOW/NVO/TSLA reports)
2. `public/tools/research-desk/manifest.json` (append/update entry)

manifest.json schema:
```json
{"reports": [{"ticker":"HOOD","company":"Robinhood","date":"2026-04-27","file":"HOOD_2026-04-27.html","tape":49,"tapeLabel":"Leaning Bearish","thesis":73,"thesisLabel":"Bullish"}]}
```

### Step 9 — Create `.github/workflows/research-desk.yml`
- workflow_dispatch with input: ticker (string)
- pip install yfinance pandas pandas-ta requests google-generativeai
- python scripts/research_desk.py "${{ inputs.ticker }}" (GEMINI_API_KEY + ADANOS_API_KEY from secrets)
- git commit + push HTML + manifest.json

### Step 10 — Rewrite `public/tools/research-desk/index.html`
- On load: fetch manifest.json → render cards sorted by date desc
- "Run New Report" button → input dialog for ticker → trigger research-desk.yml → poll → refresh manifest → new card appears
- Each card: ticker, company, date, Tape score ring, Thesis score ring, click → opens report HTML

### Step 11 — Create manifest.json seed
Seed with 4 existing reports (HOOD, NOW, NVO, TSLA) with their known scores.

### Step 12 — Delete old files
- `scripts/scan.py` → deleted (replaced by stock_scan.py)
- `scripts/generate_report.py` → deleted (replaced by research_desk.py)
- `.github/workflows/weekly-scan.yml` → deleted
- `.github/workflows/generate-report.yml` → deleted

### Step 13 — npm run build + git push
- Verify build passes
- Push to main → deploy.yml auto-runs

---

## Polling Pattern (copy-paste for all 3 tools)
```js
const GH_TOKEN = 'GH_PAT_REDACTED';
const REPO = 'sampandey404/LuNova-Trades';

async function triggerWorkflow(workflowFile, inputs = {}) {
  await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflowFile}/dispatches`, {
    method: 'POST',
    headers: { 'Authorization': `token ${GH_TOKEN}`, 'Content-Type': 'application/json', 'Accept': 'application/vnd.github.v3+json' },
    body: JSON.stringify({ ref: 'main', inputs })
  });
}

async function pollForCompletion(setStatus) {
  await sleep(10000); // wait for run to register
  const start = Date.now();
  while (Date.now() - start < 300000) { // 5 min timeout
    const res = await fetch(`https://api.github.com/repos/${REPO}/actions/runs?event=workflow_dispatch&per_page=1`,
      { headers: { 'Authorization': `token ${GH_TOKEN}` } });
    const run = (await res.json()).workflow_runs[0];
    if (!run) { await sleep(5000); continue; }
    setStatus(`Running… (${run.status})`);
    if (run.status === 'completed') {
      if (run.conclusion === 'success') return { ok: true };
      return { ok: false, logUrl: run.html_url, conclusion: run.conclusion };
    }
    await sleep(5000);
  }
  return { ok: false, logUrl: null, conclusion: 'timeout' };
}
```

---

## API Keys / Secrets
- Adanos: `sk_live_da15ca691fc131961faf2587b8e7f0f5` → Secret `ADANOS_API_KEY` ✅ added
- Gemini: `AIzaSyAdZMsaCeX0eJCuIdKN7TcngxglAcSkjss` → Secret `GEMINI_API_KEY` ⬅ NEEDS ADDING (Step 1)
- PAT: `GH_PAT_REDACTED` → in browser JS

## Key Source Files (originals, do not modify)
- Wheel Tracker source: `/Users/s.r.p/Library/Application Support/Claude/local-agent-mode-sessions/b5e2a608-a214-49d9-96cc-4f79f54190b1/3e9c74f6-8e82-4bd9-86d0-b45125b0478e/local_f93be577-9688-40d7-b36c-3573484f1865/outputs/wheel-tracker-clean.html`
- Stock Picks source: `/Users/s.r.p/Documents/Claude/Artifacts/beat-up-recovery-scanner/index.html`
- Research Desk reports: `/Users/s.r.p/Documents/Claude/Projects/Quant Research Desk/`
- Research Desk system instructions: `/Users/s.r.p/Documents/Claude/Projects/Quant Research Desk/SYSTEM_INSTRUCTIONS_v2.md`
