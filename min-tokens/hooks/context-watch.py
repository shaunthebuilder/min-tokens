#!/usr/bin/env python3
"""min-tokens UserPromptSubmit hook.

Two jobs, both fail-silent (exit 0 — a hook must never block a session):
  1. Answer /min-tokens, /min-tokens off, /min-tokens on locally and BLOCK the
     prompt (decision:"block") so the model never runs — zero model tokens.
     /min-tokens save passes through: it needs the model to summarize the convo.
  2. Otherwise, warn ONLY when context crosses a ceiling.

Command interception mirrors caveman-stats: match the RAW typed slash text
(including the plugin-namespaced `/min-tokens:min-tokens` form). If Claude Code
expands the skill before this hook sees the prompt, the match simply misses and
the SKILL.md answers instead — same behavior as before, never worse.
"""
import sys, os, re, json, subprocess

SOFT = int(os.environ.get("MIN_TOKENS_SOFT", "80000"))
HARD = int(os.environ.get("MIN_TOKENS_HARD", "120000"))

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
OFF_FLAG = os.path.join(CONFIG_DIR, ".min-tokens-off")

# /min-tokens [arg]  or  /min-tokens:min-tokens [arg]
CMD_RE = re.compile(r"^/min-tokens(?::min-tokens)?(?:\s+(\S+))?\s*$", re.IGNORECASE)


def block(reason):
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason.strip()}))


def status_reason(transcript_path):
    scripts = os.path.join(PLUGIN_ROOT, "scripts")
    out = []
    for name, extra in (("context-size.py", []), ("usage-report.py", ["--days", "7"])):
        p = os.path.join(scripts, name)
        try:
            r = subprocess.run([sys.executable, p, *extra], capture_output=True,
                               text=True, timeout=5)
            out.append((r.stdout or r.stderr).strip())
        except Exception:
            out.append(f"({name} unavailable)")
    out.append("Highest-value action: context near/over ceiling → /min-tokens save + /clear; "
               "expensive-model share high on routine work → switch to Sonnet; else nothing.")
    return "\n".join(x for x in out if x)


def handle_command(arg, transcript_path):
    """Return True if this prompt was a min-tokens command we fully handled."""
    if arg is None:
        block(status_reason(transcript_path))
    elif arg.lower() == "off":
        try:
            open(OFF_FLAG, "w").close()
        except Exception:
            pass
        block("min-tokens off — session-start injection disabled from next session. "
              "Stop applying the rules now. Re-enable: /min-tokens on")
    elif arg.lower() == "on":
        try:
            os.path.exists(OFF_FLAG) and os.remove(OFF_FLAG)
        except Exception:
            pass
        block("min-tokens on — rules resume next session.")
    else:
        return False  # 'save' and anything else pass through to the skill/model
    return True


def last_assistant_usage(path):
    # min: read only the tail (~200KB) — the last assistant turn is always near EOF; avoids O(file) on 600K-token sessions.
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


def main():
    data = json.loads(sys.stdin.read().lstrip("﻿"))
    tp = data.get("transcript_path")

    m = CMD_RE.match((data.get("prompt") or "").strip())
    if m and handle_command(m.group(1), tp):
        return  # command handled + blocked; skip the ceiling warning

    if not tp or not os.path.exists(tp):
        return
    u = last_assistant_usage(tp)
    if not u:
        return
    ctx = (u.get("input_tokens", 0)
           + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    k = ctx // 1000
    if ctx >= HARD:
        print(f"⚠ context ~{k}K (over hard ceiling) — STOP: run /min-tokens save, then /clear before continuing.")
    elif ctx >= SOFT:
        print(f"⚠ context ~{k}K — wrap up this task, then /min-tokens save + /clear (not /compact).")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a hook must never block a session
