#!/usr/bin/env python3
"""min-tokens usage report: weighted token usage from local Claude Code transcripts (zero deps)."""
import json, os, glob, argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# $/MTok proxies for limit weighting (input, output, cache_write, cache_read). Update when pricing changes.
PRICE = {
    "opus":   (5.0, 25.0, 6.25, 0.50),
    "fable":  (10.0, 50.0, 12.5, 1.00),
    "sonnet": (3.0, 15.0, 3.75, 0.30),
    "haiku":  (1.0, 5.0, 1.25, 0.10),
}


def tier(model):
    for k in PRICE:
        if k in model:
            return k
    return "sonnet"


def main(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    agg = defaultdict(lambda: defaultdict(float))
    msgs = 0
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            with open(path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if (rec.get("timestamp") or "")[:10] < cutoff:
                        continue
                    if rec.get("type") == "user" and not rec.get("isSidechain"):
                        msgs += 1
                    if rec.get("type") != "assistant":
                        continue
                    u = (rec.get("message") or {}).get("usage") or {}
                    if not u:
                        continue
                    m = tier((rec["message"].get("model") or ""))
                    p = PRICE[m]
                    agg[m]["calls"] += 1
                    agg[m]["cost"] += (u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1]
                                       + u.get("cache_creation_input_tokens", 0) * p[2]
                                       + u.get("cache_read_input_tokens", 0) * p[3]) / 1e6
                    agg[m]["out"] += u.get("output_tokens", 0)
                    agg[m]["cr"] += u.get("cache_read_input_tokens", 0)
        except Exception:
            continue
    total = sum(d["cost"] for d in agg.values()) or 1
    print(f"last {days}d — weighted usage (API-price proxy): ${total:,.2f}  |  user msgs: {msgs}  |  $/msg: {total / max(msgs, 1):.2f}")
    for m, d in sorted(agg.items(), key=lambda x: -x[1]["cost"]):
        print(f"  {m:<7} {int(d['calls']):>5} calls  ${d['cost']:>8,.2f} ({100 * d['cost'] / total:4.1f}%)  out={d['out'] / 1e3:,.0f}K cr={d['cr'] / 1e6:,.0f}M")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    main(ap.parse_args().days)
