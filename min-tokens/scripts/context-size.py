#!/usr/bin/env python3
"""Estimate current session context (K tokens) from the newest transcript for this project's cwd.
Usage: context-size.py [transcript.jsonl]   (defaults to newest jsonl for the current directory)."""
import sys, os, re, glob, json

SOFT = int(os.environ.get("MIN_TOKENS_SOFT", "80000"))


def newest_transcript():
    # Claude Code stores transcripts under ~/.claude/projects/<cwd-with-nonalnum-as-dash>/*.jsonl
    mangled = re.sub(r"[^a-zA-Z0-9]", "-", os.getcwd())
    d = os.path.expanduser(f"~/.claude/projects/{mangled}")
    files = glob.glob(os.path.join(d, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def last_usage(path):
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
    p = sys.argv[1] if len(sys.argv) > 1 else newest_transcript()
    if not p or not os.path.exists(p):
        print("context: unknown (no transcript found for this project)")
        return
    u = last_usage(p)
    if not u:
        print("context: unknown (no usage recorded yet)")
        return
    ctx = (u.get("input_tokens", 0)
           + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    print(f"context ~{ctx // 1000}K ({100 * ctx / SOFT:.0f}% of {SOFT // 1000}K soft ceiling)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("context: unknown")
