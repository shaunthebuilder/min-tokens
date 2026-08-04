#!/usr/bin/env python3
"""min-tokens context gauge — two zero-token surfaces onto the same number.

Both are rendered by the harness, never by the model, so neither costs a token
and neither depends on the model choosing to cooperate:

  (default) statusline mode — one plain line, re-run by Claude Code on every
            conversation update. Always visible, never interrupts.
  --stop    Stop-hook mode — emits {"systemMessage": ...} so the same bar lands
            under each response. Silent below GAUGE_AT so it is not per-turn
            noise; the statusline already covers the quiet range.

Both read the live context size out of the transcript exactly the way
context-watch.py does (last assistant turn's usage block). Deliberately NOT
imported from context-watch.py: that file is covered by the 58-test suite and
carries the ceiling ladder: a shared-helper refactor would put a statusline that
runs every 300ms in the same blast radius as the prompt-blocking path. Seventeen
duplicated lines are the cheaper risk.

Fail-silent: any error prints nothing and exits 0. A broken statusline must
never break a session, and a broken Stop hook must never trap a turn.
"""
import sys, os, json, re, datetime

# Style-contract compliance log (Stop mode only). Silent, zero tokens, never
# shown to the user: it answers "is the response contract being followed" on day
# two instead of at the fortnight review. Regexes are copied verbatim from
# scripts/style-ab.py so the log and the fortnight instrument cannot disagree.
STYLE_LOG = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                         "min-tokens-style.log")
FENCE = re.compile(r"```.*?```", re.S)
PREAMBLE = re.compile(r"^\s*(i'?ll\b|let me\b|i'?m going to\b|i will\b|first,|looking at\b|"
                      r"i'?ve been\b|now i'?ll\b|to (do|answer) (this|that)\b|"
                      r"here'?s what i\b|i need to\b|let'?s\b)", re.I)
VERDICT = re.compile(r"^\s*(done\b|fixed\b|yes\b|no\b|not\b|it (is|does|works|doesn'?t)|"
                     r"\W*\d|the (answer|cause|problem|number|verdict)\b|"
                     r"all \w+ pass|passed?\b|fail(ed|s)?\b|works\b|nothing\b|"
                     r"you (can|need|have|should)\b|there (is|are|was|were)\b|"
                     r"that'?s\b|this (is|does)\b|both\b|neither\b|confirmed\b)", re.I)

HARD = int(os.environ.get("MIN_TOKENS_HARD", "150000"))
ASK_AT = int(os.environ.get("MIN_TOKENS_SOFT", "95000"))
AUTO_AT = int(os.environ.get("MIN_TOKENS_AUTO", "125000"))
GAUGE_AT = int(os.environ.get("MIN_TOKENS_GAUGE_AT", "50000"))
CELLS = int(os.environ.get("MIN_TOKENS_GAUGE_CELLS", "10"))


def last_assistant_usage(path):
    # min: tail-read ~200KB — the last assistant turn is always near EOF. Same
    # bound as context-watch.py; matters more here (statusline runs constantly).
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 200_000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") == "assistant":
            u = (rec.get("message") or {}).get("usage")
            if u:
                return u
    return None


def last_assistant_text(path):
    """The reply the user actually reads: last assistant text block, code stripped."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 200_000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            txt = "\n".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
        else:
            continue
        txt = FENCE.sub(" ", txt).strip()
        if txt:
            return txt
    return ""


def log_style(path):
    txt = last_assistant_text(path)
    if not txt:
        return
    first = txt.splitlines()[0]
    with open(STYLE_LOG, "a") as f:
        f.write("%s\t%d\t%s\t%s\n" % (
            datetime.datetime.now().isoformat(timespec="seconds"),
            len(txt.split()),
            "verdict" if VERDICT.match(first) else "-",
            "preamble" if PREAMBLE.match(first) else "-"))


def ctx_tokens(path):
    if not path or not os.path.exists(path):
        return None
    u = last_assistant_usage(path)
    if not u:
        return None
    return (u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0))


def render(ctx):
    """The bar scales to HARD, so a full bar means 'stop', not 'nearly full'."""
    filled = max(0, min(CELLS, round(CELLS * ctx / HARD)))
    bar = "▓" * filled + "░" * (CELLS - filled)
    s = f"ctx {bar} {ctx // 1000}K"
    if ctx >= HARD:
        s += "  STOP · save + new thread"
    elif ctx >= AUTO_AT:
        s += "  save + new thread"
    elif ctx >= ASK_AT:
        s += "  save soon"
    return s


def main():
    # Our own recovery child is a real Claude Code session and fires Stop too.
    # Its stdout is parsed by recover.py (must start "# STATE"), so a stray
    # systemMessage there could corrupt a state.md rewrite. Same load-bearing
    # guard as session-start.sh / recover.py — never remove.
    if os.environ.get("MIN_TOKENS_CHILD"):
        return
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    if os.path.exists(os.path.join(config_dir, ".min-tokens-off")):
        return
    data = json.loads(sys.stdin.read().lstrip("﻿"))
    tp = data.get("transcript_path")
    if "--stop" in sys.argv and tp and os.path.exists(tp):
        try:
            log_style(tp)
        except Exception:
            pass  # the log must never cost a turn
    ctx = ctx_tokens(tp)
    if ctx is None:
        return
    if "--stop" in sys.argv:
        if ctx >= GAUGE_AT:
            sys.stdout.write(json.dumps({"systemMessage": render(ctx)}))
        return
    sys.stdout.write(render(ctx))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a gauge must never break a session
