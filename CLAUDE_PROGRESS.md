# LuNova Trades — Claude Build Progress

## Repo
`https://github.com/sampandey404/LuNova-Trades.git`
Local path: `~/LuNova-Trades/`
Live URL: `https://sampandey404.github.io/LuNova-Trades/`

---

## Status: ~90% Complete

### ✅ Done
1. **Vite React scaffold** — `npm create vite@latest` run, `node_modules` installed
2. **vite.config.js** — `base: '/LuNova-Trades/'` set
3. **Nav shell** — `src/App.jsx`, `src/App.css` (dark #0a0e27 nav, gold accent, 3 iframe tabs)
4. **Wheel Tracker** — `public/tools/wheel-tracker.html`
   - All 5 `window.cowork` calls replaced with direct `fetch()`:
     - `get_stock_info` → `query1.finance.yahoo.com/v8/finance/chart/{ticker}` via corsproxy.io
     - `get_option_expiration_dates` → `query2.finance.yahoo.com/v7/finance/options/{ticker}`
     - `get_option_chain` → same + `?date={epoch}`
     - `askClaude` fallback → removed (Yahoo chart meta always has price)
     - Google Drive MCP → Google Sheets CSV export (`/export?format=csv&gid={tab}`)
   - Schedule button → clipboard copy instead of chat-send
   - `GDRIVE_READ` constant removed, `YF_PROXY` added
   - ⚠️ **Requires:** Sheet must be shared "Anyone with link → Viewer"
5. **Stock Picks** — `public/tools/stock-picks/index.html`
   - All hardcoded cards replaced with dynamic `fetch('./results.json')` rendering
   - `results.json` seeded with May 4 2026 scan data (0 active, 10 onDeck, 7 monitor)
6. **scan.py** — `scripts/scan.py`
   - Full weekly scan: Finviz → tradingview_ta (weekly) → 6 hard filters → 0-13 score → Adanos social → results.json
   - ADANOS_API_KEY read from env var
7. **Research Desk seed** — 4 HTML reports copied to `public/tools/research-desk/`
8. **Research Desk index** — `public/tools/research-desk/index.html`
   - Card grid: HOOD (49/73), NOW (30/83), NVO (63/69), TSLA (44/73)
9. **generate_report.py** — `scripts/generate_report.py`
   - Full Research Desk pipeline: TradingView + Yahoo Finance + Adanos + StockTwits
   - Computes Tape/Thesis scores, outputs self-contained HTML to `public/tools/research-desk/`

### 🔲 Remaining (pick up here)
10. **GitHub Actions workflows** — need to create:
    - `.github/workflows/deploy.yml` — push to main → `npm run build` → deploy dist/ to gh-pages
    - `.github/workflows/weekly-scan.yml` — Sunday noon UTC → `python scripts/scan.py` → commit results.json
    - `.github/workflows/generate-report.yml` — manual `workflow_dispatch` with ticker input → `python scripts/generate_report.py $TICKER` → commit HTML

11. **npm run build** — verify build succeeds, check for errors

12. **Push to GitHub** — `git add -A && git commit -m "Initial build" && git push`
    - If HTTPS auth fails: user needs to run `gh auth login` or use SSH
    - Alternative: zip the folder and user uploads manually

13. **GitHub repo settings** — enable Pages, set source to `gh-pages` branch

14. **GitHub Secrets** — add `ADANOS_API_KEY = sk_live_da15ca691fc131961faf2587b8e7f0f5`

---

## Key File Map
```
~/LuNova-Trades/
├── src/
│   ├── App.jsx          ← nav shell (3 tabs)
│   ├── App.css          ← nav styles
│   └── main.jsx         ← Vite entry (unchanged from scaffold)
├── public/tools/
│   ├── wheel-tracker.html          ← refactored (no cowork)
│   ├── stock-picks/
│   │   ├── index.html              ← dynamic (loads results.json)
│   │   └── results.json            ← seed data, updated by scan.py
│   └── research-desk/
│       ├── index.html              ← listing page (4 cards)
│       ├── HOOD_2026-04-27.html
│       ├── NOW_2026-04-24.html
│       ├── NVO_2026-04-24.html
│       └── TSLA_2026-04-24.html
├── scripts/
│   ├── scan.py                     ← weekly scan (Stock Picks)
│   └── generate_report.py          ← on-demand Research Desk report
├── vite.config.js
├── package.json
└── .github/workflows/              ← STILL NEEDS CREATING
    ├── deploy.yml
    ├── weekly-scan.yml
    └── generate-report.yml
```

## Source Files (original artifacts)
- Wheel Tracker: `/Users/s.r.p/Library/Application Support/Claude/local-agent-mode-sessions/b5e2a608-a214-49d9-96cc-4f79f54190b1/3e9c74f6-8e82-4bd9-86d0-b45125b0478e/local_f93be577-9688-40d7-b36c-3573484f1865/outputs/wheel-tracker-clean.html`
- Stock Picks: `/Users/s.r.p/Documents/Claude/Artifacts/beat-up-recovery-scanner/index.html`
- Research Desk: `/Users/s.r.p/Documents/Claude/Projects/Quant Research Desk/`

## API Keys / Secrets
- Adanos: `sk_live_da15ca691fc131961faf2587b8e7f0f5` → GitHub Secret `ADANOS_API_KEY`
- Google Sheet ID: `1OWAbBoEKdZroEV0CiJnV8gib6FrE212LL1tIo4vNaag`
- CORS proxy for Wheel Tracker: `https://corsproxy.io/?url=`

## Pre-Req Still Needed From User
- **Make Google Sheet public:** Google Sheets → Share → Anyone with link → Viewer
- **Push to GitHub:** run `git push` with auth (HTTPS or SSH)
- **Enable Pages:** repo Settings → Pages → gh-pages branch
- **Add Secret:** repo Settings → Secrets → ADANOS_API_KEY
