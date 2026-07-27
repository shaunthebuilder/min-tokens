#!/usr/bin/env python3
"""min-tokens PostToolUse hook: report the context cost of a large tool result.

Reports a NUMBER, never a directive. A hook that discourages reading is a hook
that degrades answers; this one states what the read cost and lets the model
weigh it. Fires at most MAX_FIRES times per session, only above THRESHOLD.

Size estimate: `tool_response` when the payload carries it (accurate), else the
transcript's byte growth since the previous tool call (approximate but always
available — `transcript_path` is a common input field). Fail-silent, exit 0.
"""
import sys, os, json, time

THRESHOLD = int(os.environ.get("MIN_TOKENS_RESULT_WARN", "10000"))  # tokens
MAX_FIRES = 3
WATCH = {"Read", "Bash", "Grep", "Glob", "WebFetch", "NotebookRead"}

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
OFF_FLAG = os.path.join(CONFIG_DIR, ".min-tokens-off")
WARN_DIR = os.path.join(CONFIG_DIR, ".min-tokens-warned")


def prune(now):
    for f in os.listdir(WARN_DIR):
        p = os.path.join(WARN_DIR, f)
        try:
            if now - os.path.getmtime(p) > 604800:  # 7d
                os.remove(p)
        except Exception:
            pass


def transcript_delta(sid, path):
    """Bytes appended since the previous tool call this session; None on the first."""
    marker = os.path.join(WARN_DIR, f"{sid}-tsize")
    size = os.path.getsize(path)
    prev = None
    try:
        prev = int(open(marker).read().strip())
    except Exception:
        pass
    with open(marker, "w") as f:
        f.write(str(size))
    return None if prev is None or size <= prev else size - prev


def main():
    data = json.loads(sys.stdin.read().lstrip("﻿"))
    if os.path.exists(OFF_FLAG) or data.get("tool_name") not in WATCH:
        return
    sid = data.get("session_id") or "unknown"

    os.makedirs(WARN_DIR, exist_ok=True)
    prune(time.time())

    resp = data.get("tool_response")
    if resp is not None:
        chars = len(resp if isinstance(resp, str) else json.dumps(resp))
    else:
        tp = data.get("transcript_path")
        chars = transcript_delta(sid, tp) if tp and os.path.exists(tp) else None
    if not chars:
        return

    tok = chars // 4
    if tok < THRESHOLD:
        return

    fired = [f for f in os.listdir(WARN_DIR) if f.startswith(f"{sid}-rsize-")]
    if len(fired) >= MAX_FIRES:
        return
    open(os.path.join(WARN_DIR, f"{sid}-rsize-{len(fired) + 1}"), "w").close()

    k = tok // 1000
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"⚠ that result added ~{k}K tokens to context permanently "
            f"(~{k * 2}K cache write, then ~{max(1, k // 10)}K every later turn)."
        ),
    }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a hook must never block a session
