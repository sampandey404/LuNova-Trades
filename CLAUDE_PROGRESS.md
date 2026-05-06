# LuNova-Trades — Claude Resume Checkpoint
_Last updated: 2026-05-06_

## Project Goal
Self-standing GitHub Pages webapp at `sampandey404.github.io/LuNova-Trades` that replicates three CoWork products with NO Claude token usage. GitHub Actions is the compute engine. Only AI component = free Gemini call in Research Desk.

## Architecture
- Browser → GitHub API `workflow_dispatch` → Python on Actions runner → results committed as JSON/HTML → browser polls & renders
- PAT stored in browser `localStorage` under key `gh_pat_v3`
- Static site: `public/` folder, GitHub Pages root

## Three Products
1. **Wheel Income Tracker** — `wheel-scan.yml` → `wheel_scan.py` → `wheel-results.json` → `wheel-tracker.html`
2. **Stock Picks Scanner** — `stock-scan.yml` → `stock_scan.py` → `results.json` → `stock-picks/index.html`
3. **Research Desk** — `research-desk.yml` (needs `ticker` input) → `research_desk.py` → per-ticker HTML + `manifest.json` → `research-desk/index.html`

## GitHub Secrets (all added ✅)
- `ADANOS_API_KEY` = `sk_live_da15ca691fc131961faf2587b8e7f0f5`
- `GEMINI_API_KEY` = `AIzaSyAdZMsaCeX0eJCuIdKN7TcngxglAcSkjss` ← added 2026-05-06

## External Data Sources
- **Google Sheets** (no auth CSV export): `https://docs.google.com/spreadsheets/d/1OWAbBoEKdZroEV0CiJnV8gib6FrE212LL1tIo4vNaag/export?format=csv&gid=`
  - Positions: `gid=0`, History: `gid=424579761`, YTD: `gid=606643665`, Monthly: `gid=248139668`
- **GitHub raw**: `https://raw.githubusercontent.com/sampandey404/LuNova-Trades/main/...`
- **Adanos API**: `https://api.adanos.org/{platform}/stocks/v1/stock/{ticker}` with `X-API-Key`
- **Gemini**: model `gemini-1.5-flash`, key from `GEMINI_API_KEY` env

## Fixes Applied (2026-05-06) — ALL COMMITTED ✅

### research_desk.py
- **FIXED**: `above_ema20` / `above_ema200` NameError — variables were referenced at line ~441 but only defined at line ~506. Moved definitions immediately before `ta_signals_list = []`.

### wheel_scan.py
- **FIXED**: `next_weekly_expiry()` only picked Fridays (`weekday()==4`). Changed to `weekday() in (0, 2, 4)` to include Mon/Wed/Fri expirations (QQQ/SPY/GOOGL have all three).

### stock_scan.py
- **FIXED**: Bucketing bug — `on_deck` list was never populated. Now: `tech >= 6` → active, `tech 4-5` → on_deck, `tech == 3` → monitor, `tech < 3` → discarded.
- **FIXED**: `fval()` helper made more robust (try/except instead of inline lambda).
- **IMPROVED**: Added `traceback.print_exc()` in the exception handler for better GitHub Actions log visibility.

### public/tools/stock-picks/index.html
- **FIXED**: `buildCard()` — `drawdown` formatted as `-33.5%` (was raw float), `macd` shown as up/down triangle + value (was raw float), `thesis` used instead of `note`, `social` object rendered as readable text instead of [object Object].
- **ADDED**: `signals` badges, company name, stage number in card header.

## What Works Now
- Wheel Tracker scan: WORKING (confirmed 10 real picks May 6)
- Wheel Tracker Google Sheet CSV: ALREADY FIXED (direct export URLs, no CoWork dependency)
- Research Desk UI + manifest rendering: WORKING (6 reports: OKLO/ORCL/HOOD/NOW/NVO/TSLA)
- Research Desk new report generation: NOW FIXED (NameError resolved)
- Stock Picks scan: FIXED (bucketing + robustness improved)
- Stock Picks UI: FIXED (buildCard format mismatches resolved)

## Remaining Items
- **End-to-end test**: Trigger each workflow from the GitHub Pages UI, verify results render correctly
- If stock scan still returns empty: check Actions log for specific exception from `analyze()` — likely a yfinance API change or ta library version issue

## Key File Locations
- Scripts: `scripts/research_desk.py`, `scripts/stock_scan.py`, `scripts/wheel_scan.py`
- Workflows: `.github/workflows/research-desk.yml`, `stock-scan.yml`, `wheel-scan.yml`
- HTML: `public/tools/wheel-tracker.html`, `public/tools/stock-picks/index.html`, `public/tools/research-desk/index.html`
- Results: `public/tools/wheel-tracker/wheel-results.json`, `public/tools/stock-picks/results.json`, `public/tools/research-desk/manifest.json`
