#!/usr/bin/env python3
"""min-tokens cost falsifier — did the plugin save more than it burned?

Reads ~/.claude/projects/*/*.jsonl offline. Zero model tokens.

The plugin costs tokens (the injected rules block, carried every turn) and is
supposed to save more (shorter replies, smaller reads, state.md handoffs that
avoid re-exploring). Both sides land in the same place: the usage block on every
assistant turn. So the whole question is one number, measured before and after
the block started being injected.

UNIT: weighted $ per TYPED USER TURN. Per-session totals are useless on their
own — a long session costs more because it is long, not because it is wasteful.
Normalising by the turns you actually typed asks the only question that matters:
what does one instruction from you cost?

CONFOUND, and it is a big one: a shift in model mix moves this number far more
than any prompt-level economy can. Opus output is 5x Sonnet output. The mix is
therefore printed alongside every window, and a verdict that ignores it is
worthless. Same for context growth: longer threads cost more per turn whatever
the rules say.

  python3 min-tokens/scripts/cost-ab.py              # pivot at first injection
  AB_PIVOT=2026-08-03 python3 .../cost-ab.py         # any other split
"""
import glob
import json
import os
import sys
from collections import defaultdict

PROJECTS = os.path.expanduser("~/.claude/projects")
PIVOT = os.environ.get("AB_PIVOT", "2026-07-24")  # first session carrying the rules block

# $/MTok (input, output, cache_write, cache_read). Mirrors usage-report.py.
PRICE = {
    "opus":   (5.0, 25.0, 6.25, 0.50),
    "fable":  (10.0, 50.0, 12.5, 1.00),
    "sonnet": (3.0, 15.0, 3.75, 0.30),
    "haiku":  (1.0, 5.0, 1.25, 0.10),
}


def tier(model):
    for k in PRICE:
        if k in (model or ""):
            return k
    return "sonnet"


def typed(msg):
    """A turn the user actually typed: a plain string, not a tool result."""
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip()) and not c.startswith("<")
    return False


def scan(path):
    cost = 0.0
    turns = 0
    ctx_max = 0
    mix = defaultdict(float)
    day = None
    for line in open(path, errors="replace"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        ts = rec.get("timestamp") or ""
        if ts and day is None:
            day = ts[:10]
        t = rec.get("type")
        if t == "user" and typed(rec.get("message")):
            turns += 1
        elif t == "assistant":
            m = rec.get("message") or {}
            u = m.get("usage")
            if not u:
                continue
            pi, po, pw, pr = PRICE[tier(m.get("model"))]
            inp = u.get("input_tokens", 0)
            out = u.get("output_tokens", 0)
            cw = u.get("cache_creation_input_tokens", 0)
            cr = u.get("cache_read_input_tokens", 0)
            c = (inp * pi + out * po + cw * pw + cr * pr) / 1e6
            cost += c
            mix[tier(m.get("model"))] += c
            ctx_max = max(ctx_max, inp + cw + cr)
    return day, cost, turns, ctx_max, mix


def main():
    before, after = [], []
    for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            day, cost, turns, ctx, mix = scan(p)
        except Exception:
            continue
        if not day or turns == 0 or cost == 0:
            continue  # no typed turns means nothing to normalise by
        (before if day < PIVOT else after).append((day, cost, turns, ctx, mix))

    def report(name, rows):
        if not rows:
            print(f"{name}: no sessions")
            return None
        cost = sum(r[1] for r in rows)
        turns = sum(r[2] for r in rows)
        per = cost / turns
        ctxs = sorted(r[3] for r in rows)
        med_ctx = ctxs[len(ctxs) // 2]
        mix = defaultdict(float)
        for r in rows:
            for k, v in r[4].items():
                mix[k] += v
        share = "  ".join(f"{k} {100*v/cost:.0f}%" for k, v in
                          sorted(mix.items(), key=lambda x: -x[1]))
        print(f"{name}: {len(rows)} sessions, {turns} typed turns, ${cost:.2f}")
        print(f"    ${per:.4f} per typed turn   median peak context {med_ctx//1000}K")
        print(f"    model mix by spend: {share}")
        return per

    print(f"pivot {PIVOT} (first session carrying the rules block)\n")
    b = report("BEFORE", before)
    print()
    a = report("AFTER ", after)
    if b and a:
        d = (a - b) / b * 100
        print(f"\nper-turn cost {'up' if d > 0 else 'down'} {abs(d):.0f}%")
        print("Read this against the model mix and context lines above before "
              "attributing any of it to the plugin.")


if __name__ == "__main__":
    sys.exit(main())
