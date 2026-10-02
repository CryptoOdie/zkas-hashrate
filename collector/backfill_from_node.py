"""One-time backfill of ZKAS hashrate from launch, using a synced ZKAS node.

A node keeps the headers of its pruning proof: for each block level L, a chain of blocks of level
>= L running back towards genesis, linked through each header's parents at level L. Each header
carries the chain's cumulative work (blueWork) and a timestamp, so walking one level's chain gives
a sample of total work every 2^L blocks or so. The difference in work between two samples, over the
time between them, is the real average hashrate in that span.

Usage: python collector/backfill_from_node.py ws://127.0.0.1:18110 [level]
"""
import asyncio
import json
import os
import sys

import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "history.json")


def parents_at(header, level):
    p = header.get("parentsByLevel") or header.get("parents") or []
    if level >= len(p):
        return []
    entry = p[level]
    return entry.get("parentHashes", []) if isinstance(entry, dict) else entry


def work(h):
    bw = h["blueWork"]
    return int(bw, 16) if isinstance(bw, str) and not bw.isdigit() else int(bw)


async def main(url, level):
    async with websockets.connect(url, max_size=64 * 1024 * 1024) as ws:
        n = 0

        async def call(method, params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == n:
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    return msg.get("params") or msg.get("result")

        async def header(h):
            try:
                return (await call("getBlock", {"hash": h, "includeTransactions": False}))["block"]["header"]
            except RuntimeError:
                return None

        info = await call("getBlockDagInfo", {})
        cur = await header(info["pruningPointHash"])
        samples = []
        while cur:
            samples.append({"hash": cur["hash"], "ts": int(cur["timestamp"]), "daa": int(cur["daaScore"]),
                            "blue": int(cur["blueScore"]), "bw": str(work(cur)), "bits": cur["bits"], "src": f"own node, level {level}"})
            if int(cur["daaScore"]) == 0:
                break
            best = None
            for p in parents_at(cur, level):
                h = await header(p)
                if h and (best is None or int(h["blueScore"]) > int(best["blueScore"])):
                    best = h
            cur = best
            if len(samples) % 200 == 0:
                print("  ...", len(samples), "samples, back to daa", samples[-1]["daa"])
        print(f"level {level}: {len(samples)} samples, oldest daa {samples[-1]['daa'] if samples else None}")

    data = json.load(open(PATH, encoding="utf-8"))
    known = {p["hash"] for p in data["pruningPoints"]}
    added = [s for s in samples if s["hash"] not in known and s["daa"] > 0]
    data["pruningPoints"] = sorted(data["pruningPoints"] + added, key=lambda p: p["ts"])
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print("added", len(added), "-> total work samples", len(data["pruningPoints"]))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:18110", int(sys.argv[2]) if len(sys.argv) > 2 else 12))
