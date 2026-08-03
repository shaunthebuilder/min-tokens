#!/usr/bin/env python3
"""state.md A/B falsifier — does a state.md reduce re-exploration?

Reads ~/.claude/projects/*/*.jsonl offline. Zero model tokens.
Metric: per session, Read/Grep/Glob calls AND bytes of tool_result they
returned, over the first N user turns (default 10) — the orientation phase.
Buckets by whether the session's project has a .claude/state.md TODAY.

Caveat baked into the output: presence-today is a proxy for presence-then.
"""
import json
import os
import sys
from pathlib import Path

TURNS = int(os.environ.get("AB_TURNS", "10"))
EXPLORE = {"Read", "Grep", "Glob"}
PROJECTS = Path.home() / ".claude" / "projects"


def scan(path):
    """-> (n_explore_calls, result_bytes) over the first TURNS user turns."""
    turns, calls, nbytes = 0, 0, 0
    live = set()  # tool_use ids we're counting results for
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isSidechain"):
            continue
        t, msg = d.get("type"), d.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            content = []
        if t == "user":
            # a real user turn has text, not just tool results
            if any(c.get("type") == "text" for c in content if isinstance(c, dict)) or isinstance(msg.get("content"), str):
                turns += 1
                if turns > TURNS:
                    break
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in live:
                    body = c.get("content")
                    nbytes += len(body if isinstance(body, str) else json.dumps(body))
        elif t == "assistant":
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in EXPLORE:
                    calls += 1
                    live.add(c.get("id"))
    return (calls, nbytes) if turns else None


def cwd_of(path):
    try:
        with path.open(errors="replace") as fh:
            for _ in range(40):
                line = fh.readline()
                if not line:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("cwd"):
                    return d["cwd"]
    except OSError:
        pass
    return None


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return 0 if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def main():
    if not PROJECTS.is_dir():
        sys.exit(f"no transcripts at {PROJECTS}")
    has, without, seen = [], [], {}
    for f in PROJECTS.glob("*/*.jsonl"):
        r = scan(f)
        if r is None:
            continue
        cwd = cwd_of(f)
        if not cwd:
            continue
        if cwd not in seen:
            seen[cwd] = (Path(cwd) / ".claude" / "state.md").is_file()
        (has if seen[cwd] else without).append(r)

    def row(label, rows):
        if not rows:
            return f"  {label:<16}{'—':>10}"
        calls = [c for c, _ in rows]
        kb = [b / 1024 for _, b in rows]
        return (f"  {label:<16}{len(rows):>9}{sum(calls)/len(calls):>12.1f}"
                f"{median(calls):>9.0f}{sum(kb)/len(kb):>11.0f}")

    print(f"\nstate.md A/B — first {TURNS} user turns per session\n")
    print(f"  {'':<16}{'sessions':>9}{'mean calls':>12}{'med':>9}{'mean KB':>11}")
    print(row("with state.md", has))
    print(row("without", without))

    if has and without:
        mh = sum(c for c, _ in has) / len(has)
        mw = sum(c for c, _ in without) / len(without)
        kh = sum(b for _, b in has) / len(has) / 1024
        kw = sum(b for _, b in without) / len(without) / 1024
        dc = (mh - mw) / mw * 100 if mw else 0
        dk = (kh - kw) / kw * 100 if kw else 0
        print(f"\n  difference{'':<6}{'':>9}{dc:>+11.0f}%{'':>9}{dk:>+10.0f}%")
        worst = max(dc, dk)
        if worst > -10:
            v = "NO EFFECT — state.md is not replacing exploration. Shrink it."
        elif worst > -25:
            v = "WEAK — some reduction, smaller than state.md's own resident cost."
        else:
            v = "WORKING — state.md is paying for itself. Keep it."
        print(f"\n  VERDICT: {v}")
    projects = sum(1 for v in seen.values() if v)
    print(f"\n  ({projects} of {len(seen)} projects have a state.md today — "
          f"presence-today is a proxy for presence-then.)\n")


if __name__ == "__main__":
    main()
