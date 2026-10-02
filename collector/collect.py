"""Hourly collector for the ZKAS hashrate site. Run by .github/workflows/collect.yml.

Every run it reads each independent source, records what each one says, and appends to
data/history.json. Nothing is overwritten: a source that is down simply leaves its field empty
for that hour.

Sources
- explorer.zkas.info, read three independent ways:
    * pruning-point headers: every ~36 h of chain carries its cumulative work (blueWork). The
      difference between two of them over the time between them is the real average hashrate,
      not an estimate. The explorer only keeps recent ones, so each run stores any new ones.
    * current difficulty (hashrate = difficulty x 2 at 1 block per second)
    * the 15-minute pulse (hashrate measured from recently accepted blocks)
- mining-pool.zkas.info: the pool's own view of network hashrate, and the ZKAS/USD and KAS/USD price
- api.kaspa.org: Kaspa hashrate, block reward and price history
"""
import datetime as dt
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "history.json")
GENESIS_MS = 1785079572000  # ZKAS mainnet genesis (consensus/core/src/config/genesis.rs)
UA = {"User-Agent": "zkas-hashrate-collector (+https://github.com/CryptoOdie/zkas-hashrate)"}
EXPLORER = "https://explorer.zkas.info/api"


def get(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45))
    except Exception as e:  # one source down must not stop the others
        print(f"  ! {url}: {e}")
        return None


def load():
    if os.path.exists(PATH):
        return json.load(open(PATH, encoding="utf-8"))
    return {"version": 1, "genesis": GENESIS_MS, "pruningPoints": [], "samples": [], "kaspaDaily": [], "priceHistory": []}


def pruning_points(data, start):
    """Walk pruning-point headers back from `start` until one we already hold (or the explorer runs out)."""
    known = {p["hash"] for p in data["pruningPoints"]}
    added, h = 0, start
    while h and h not in known:
        d = get(f"{EXPLORER}/blocks/{h}?includeTransactions=false")
        if not d or "header" not in d:
            break
        hd = d["header"]
        data["pruningPoints"].append({"hash": h, "ts": int(hd["timestamp"]), "daa": int(hd["daaScore"]),
                                      "blue": int(hd["blueScore"]), "bw": str(hd["blueWork"]), "bits": hd["bits"],
                                      "src": "explorer.zkas.info"})
        known.add(h)
        added += 1
        h = hd.get("pruningPoint")
    data["pruningPoints"].sort(key=lambda p: p["ts"])
    return added


def main():
    data = load()
    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    sample = {"t": now, "zkas": {}, "kaspa": {}, "price": {}}

    dag = get(f"{EXPLORER}/info/blockdag")
    if dag:
        sample["zkas"]["difficulty"] = dag["difficulty"] * 2
        print(f"  pruning points added: {pruning_points(data, (dag.get('pruningPointHash') or [None])[0])}")
    pulse = get(f"{EXPLORER}/info/pulse?window=900")
    if pulse:
        bins = [x for x in pulse.get("workHashrateBins") or [] if x]
        if bins:
            sample["zkas"]["pulse"] = sum(bins) / len(bins)
    halving = get(f"{EXPLORER}/info/halving")
    if halving:
        sample["zkas"]["reward"] = halving["currentAmount"]
        sample["zkas"]["nextReductionTs"] = halving["nextHalvingTimestamp"]
    shielded = get(f"{EXPLORER}/info/shielded")
    if shielded:
        sample["zkas"]["notes"] = shielded.get("noteCount")
    pool = get("https://mining-pool.zkas.info/api/stats")
    if pool and pool.get("networkHashrate"):
        sample["zkas"]["pool"] = pool["networkHashrate"]
    otc = get("https://mining-pool.zkas.info/api/otc/price")
    if otc:
        sample["price"] = {"zkasUsd": otc.get("zkasUsd"), "kasUsd": otc.get("kasUsd"),
                           "zkasKas": float((otc.get("mid") or {}).get("kas") or 0) or None}

    kas_h = get("https://api.kaspa.org/info/hashrate?stringOnly=false")
    if kas_h:
        sample["kaspa"]["hashrate"] = kas_h["hashrate"] * 1e12
    kas_r = get("https://api.kaspa.org/info/blockreward")
    if kas_r:
        sample["kaspa"]["reward"] = kas_r["blockreward"]
    kas_p = get("https://api.kaspa.org/info/price")
    if kas_p and not sample["price"].get("kasUsd"):
        sample["price"]["kasUsd"] = kas_p["price"]
    data["samples"].append(sample)

    # Kaspa hashrate per day since ZKAS launch (replaced whole each run; the API keeps all of it).
    hist = get("https://api.kaspa.org/info/hashrate/history")
    if hist:
        days = {}
        for x in hist:
            if x["timestamp"] >= GENESIS_MS:
                day = dt.datetime.fromtimestamp(x["timestamp"] / 1000, dt.UTC).strftime("%Y-%m-%d")
                days.setdefault(day, []).append(x["hashrate_kh"] * 1000)
        data["kaspaDaily"] = [{"d": d, "h": sum(v) / len(v)} for d, v in sorted(days.items())]

    # ZKAS price in KAS per day (merged, so days the explorer later drops are kept).
    prices = get("https://explorer.zkas.info/price-history.json")
    if prices:
        merged = {p["t"]: p for p in data["priceHistory"]}
        merged.update({p["t"]: {"t": p["t"], "kas": p["kas"]} for p in prices})
        data["priceHistory"] = sorted(merged.values(), key=lambda p: p["t"])

    # Every OTC desk trade (Discord and Telegram), from the first one on 23 August 2026. Stored compactly
    # as [timestamp ms, price in KAS, ZKAS amount]; fetched incrementally from the newest one held.
    trades = data.get("otcTrades", [])
    since = trades[-1][0] + 1 if trades else 0
    for _ in range(200):
        page = get(f"https://mining-pool.zkas.info/api/otc/trades?since={since}")
        if not page or not page.get("trades"):
            break
        for x in page["trades"]:
            try:
                trades.append([int(x["ts"]), float(x["price"]), float(x["zkas"])])
            except (KeyError, ValueError):
                pass
        if not page.get("next") or page["next"] <= since:
            break
        since = page["next"]
    data["otcTrades"] = sorted({t[0]: t for t in trades}.values())

    # ZKAS/USDT daily candles from NonKYC, the main exchange (listed 25 September 2026).
    from_s, to_s = GENESIS_MS // 1000, now // 1000
    candles = get(f"https://api.nonkyc.io/api/v2/market/candles?symbol=ZKAS_USDT&resolution=1440&from={from_s}&to={to_s}")
    if candles and candles.get("bars"):
        merged = {c["t"]: c for c in data.get("nonkycDaily", [])}
        merged.update({b["time"]: {"t": b["time"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"], "v": b["volume"]}
                       for b in candles["bars"]})
        data["nonkycDaily"] = sorted(merged.values(), key=lambda c: c["t"])

    # ZKAS/USDT daily candles from Neoxa Exchange (listed 14 September 2026).
    neoxa = get("https://neoxa.exchange/api/exchange/candles/ZKAS_USDT?interval=1d&limit=1000")
    if neoxa and neoxa.get("candles"):
        merged = {c["t"]: c for c in data.get("neoxaDaily", [])}
        merged.update({c["time"] * 1000: {"t": c["time"] * 1000, "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"], "v": c["volume"]}
                       for c in neoxa["candles"]})
        data["neoxaDaily"] = sorted(merged.values(), key=lambda c: c["t"])

    # KAS/USD per day, to put the earlier OTC prices (quoted in KAS) into dollars.
    cg = get("https://api.coingecko.com/api/v3/coins/kaspa/market_chart?vs_currency=usd&days=120&interval=daily")
    if cg and cg.get("prices"):
        merged = {p["t"]: p for p in data.get("kasUsdDaily", [])}
        merged.update({int(t): {"t": int(t), "usd": usd} for t, usd in cg["prices"]})
        data["kasUsdDaily"] = sorted(merged.values(), key=lambda p: p["t"])

    data["updated"] = now
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"  samples: {len(data['samples'])}, pruning points: {len(data['pruningPoints'])}, "
          f"kaspa days: {len(data['kaspaDaily'])}, price days: {len(data['priceHistory'])}")


if __name__ == "__main__":
    main()
