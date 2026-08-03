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
import sys, os, json

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
    ctx = ctx_tokens(data.get("transcript_path"))
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
