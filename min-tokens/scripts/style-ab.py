#!/usr/bin/env python3
"""Response-contract falsifier — did the contract change how replies are written?

Reads ~/.claude/projects/*/*.jsonl offline. Zero model tokens.

Unit of measurement is THE REPLY THE USER ACTUALLY READ: the last assistant
text block before each user turn. Fenced code is stripped first — it is a
deliverable, and the contract never governs deliverables.

Why not count clarifying follow-ups directly, which is what we care about?
Measured 2026-08-03: the whole corpus holds 186 typed user turns and 13 that
match any re-ask phrasing. A fortnight-vs-fortnight test on ~7 events cannot
detect anything short of total elimination. Re-asks are still counted and
printed, but as an anecdote. The verdict rests on compliance, which has N in
the hundreds per fortnight and separates the two failure modes that matter:
the contract was ignored, versus the contract was obeyed and did not help.

  python3 min-tokens/scripts/style-ab.py                 # baseline, whole corpus
  AB_PIVOT=2026-08-04 python3 .../style-ab.py            # before vs after
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
PIVOT = os.environ.get("AB_PIVOT", "")
WINDOW = int(os.environ.get("AB_WINDOW", "14"))
MAXLEN = int(os.environ.get("AB_MAXLEN", "200"))

FENCE = re.compile(r"```.*?```", re.S)
# Law A: an opening that walks the user through process instead of answering.
PREAMBLE = re.compile(r"^\s*(i'?ll\b|let me\b|i'?m going to\b|i will\b|first,|looking at\b|"
                      r"i'?ve been\b|now i'?ll\b|to (do|answer) (this|that)\b|"
                      r"here'?s what i\b|i need to\b|let'?s\b)", re.I)
# Law A: an opening that states the answer.
VERDICT = re.compile(r"^\s*(done\b|fixed\b|yes\b|no\b|not\b|it (is|does|works|doesn'?t)|"
                     r"\W*\d|the (answer|cause|problem|number|verdict)\b|"
                     r"all \w+ pass|passed?\b|fail(ed|s)?\b|works\b|nothing\b|"
                     r"you (can|need|have|should)\b|there (is|are|was|were)\b|"
                     r"that'?s\b|this (is|does)\b|both\b|neither\b|confirmed\b)", re.I)
# Law D: forms the rules have banned since v1. 98% of hits are arrow chains.
BANNED = re.compile(r"→|⇒|(?<![\w`])(cfg|impl|fn|cfgs|deps|arg)s?(?![\w`(])")
IDENT = re.compile(r"`[^`\n]{1,60}`")
REASK = re.compile(
    r"is (it|this|that) done|done or not|did (it|that|this) work"
    r"|what does (that|this|it) mean|what do you mean|what happened"
    r"|so what|^(ok|okay)[,!. ]+ ?so\b|which (one|file|option|of)"
    r"|can (you|we) (summari[sz]e|clarify|explain|simplify|not do)"
    r"|i don'?t (understand|follow)|in short|tl;?dr|in (simple|plain)"
    r"|simpler|too long|hard to (read|follow)|readability|eyesore"
    r"|unclear|confusing|(what|whats|what's) (pending|left|next)"
    r"|explain me|so (is|are|does|do|for|the)\b", re.I)


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def pairs(path):
    """Yield (day, reply, user_turn) for every user turn that got a reply."""
    day, last = None, ""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isSidechain"):
            continue
        if day is None and d.get("timestamp"):
            try:
                day = datetime.fromisoformat(
                    d["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass
        t, msg = d.get("type"), d.get("message") or {}
        if t == "assistant":
            s = text_of(msg).strip()
            if s:
                last = s
        elif t == "user":
            s = " ".join(text_of(msg).split())
            if (not s or s.startswith("<") or s.startswith("Caveat:")
                    or s.startswith("[Request interrupted")):
                continue
            if last:
                yield day, last, s
                last = ""


def measure(replies, users):
    replies = [p for p in (FENCE.sub(" ", r).strip() for r in replies) if p]
    n = len(replies)
    if not n:
        return None
    words = sorted(len(r.split()) for r in replies)
    firsts = [r.split("\n", 1)[0] for r in replies]
    total = sum(words) or 1
    return dict(
        n=n,
        mean=total / n,
        med=words[n // 2],
        preamble=100.0 * sum(1 for f in firsts if PREAMBLE.match(f)) / n,
        verdict=100.0 * sum(1 for f in firsts if VERDICT.match(f)) / n,
        banned=100.0 * sum(len(BANNED.findall(r)) for r in replies) / n,
        ident=100.0 * sum(len(IDENT.findall(r)) for r in replies) / total,
        reask=sum(1 for u in users if len(u) <= MAXLEN and REASK.search(u)),
    )


def collect():
    if not PROJECTS.is_dir():
        sys.exit(f"no transcripts at {PROJECTS}")
    if not PIVOT:
        buckets = {"all": ([], [])}
        pivot = None
    else:
        buckets = {"before": ([], []), "after": ([], [])}
        pivot = datetime.fromisoformat(PIVOT).replace(tzinfo=timezone.utc)
    for f in PROJECTS.glob("*/*.jsonl"):
        for day, reply, user in pairs(f):
            if pivot:
                if day is None:
                    continue
                dt = (day - pivot).total_seconds()
                span = WINDOW * 86400
                k = ("after" if 0 <= dt <= span
                     else "before" if -span <= dt < 0 else None)
                if k is None:
                    continue
            else:
                k = "all"
            buckets[k][0].append(reply)
            buckets[k][1].append(user)
    return buckets


def main():
    buckets = collect()
    print("\nresponse contract — measured on the reply the user read"
          + (f", pivot {PIVOT}, {WINDOW}d each side" if PIVOT else " (baseline)") + "\n")
    print(f"  {'':<8}{'replies':>8}{'mean w':>8}{'med w':>7}"
          f"{'preamble':>10}{'verdict':>9}{'banned':>8}{'ident%':>8}{'re-ask':>8}")
    rows = {}
    for k, (r, u) in buckets.items():
        m = rows[k] = measure(r, u)
        if not m:
            print(f"  {k:<8}{'— no data':>8}")
            continue
        print(f"  {k:<8}{m['n']:>8}{m['mean']:>8.0f}{m['med']:>7}"
              f"{m['preamble']:>9.0f}%{m['verdict']:>8.0f}%"
              f"{m['banned']:>8.0f}{m['ident']:>8.1f}{m['reask']:>8}")

    b, a = rows.get("before"), rows.get("after")
    if b and a:
        if min(b["n"], a["n"]) < 150:
            v = "UNDERPOWERED — under 150 replies a side. Widen AB_WINDOW and re-run."
        elif a["verdict"] - b["verdict"] < 10 and a["banned"] > b["banned"] * 0.5:
            v = ("NOT ADOPTED — replies did not change shape at all. The contract is "
                 "not reaching the model: check style.md landed and a NEW session ran.")
        elif a["med"] >= b["med"]:
            v = ("SHAPE ONLY — the laws took, but replies are no shorter. Law B is not "
                 "doing its job; tighten scope before touching anything else.")
        else:
            v = "WORKING — shape and length both moved. Keep it."
        print(f"\n  VERDICT: {v}")

    print("\n  preamble  replies opening with 'I'll / Let me / ...'  (law A, want 0%)")
    print("  verdict   first line states the answer                (law A, want high)")
    print("  banned    arrow chains + invented abbrevs per 100      (law D, want 0)")
    print("  ident%    backticked names as a share of all words     (law D proxy)")
    print("  re-ask    raw count, tiny N — anecdote, never the verdict\n")


if __name__ == "__main__":
    main()
