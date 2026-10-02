# ZKAS hashrate

Hourly record of ZKAS network hashrate, Kaspa hashrate and ZKAS price, and the site that shows it:
**https://zkas-mining.web.app**

- `collector/collect.py` reads explorer.zkas.info (pruning-point work, difficulty, 15-minute pulse),
  mining-pool.zkas.info, NonKYC, api.kaspa.org and CoinGecko, and appends to `data/history.json`.
- `.github/workflows/collect.yml` runs it every hour and commits the new data.
- `site/` is the Firebase Hosting site. It loads `data/history.json` straight from this repo, so the
  charts keep growing without redeploys.

ZKAS hashrate between pruning points is the chain's own cumulative work divided by elapsed time, so it is a
measured average, not an estimate. Not affiliated with the ZKAS project. Not investment advice.
