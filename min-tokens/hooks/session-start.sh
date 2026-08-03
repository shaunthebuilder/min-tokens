#!/usr/bin/env bash
# min-tokens SessionStart: inject rules unless kill-switch present; note project state.md. Always exit 0.
off="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-off"
payload="$(cat)"
[ -f "$off" ] && exit 0
# Our own recovery subprocess is a Claude Code session too — inject nothing and,
# above all, do not launch another recovery from inside one.
[ -n "$MIN_TOKENS_CHILD" ] && exit 0

dir="$(cd "$(dirname "$0")" && pwd)"
cat "$dir/rules.md" 2>/dev/null

# Phase B: recover state.md from a previous thread that ended without a save.
# DETACHED with all fds closed — it takes tens of seconds (a separate `claude -p`
# call) and this hook has a 5s timeout; an inherited stdout would also hold the
# hook's pipe open. It writes state.md on disk, never into this thread's context.
printf '%s' "$payload" | nohup python3 "$dir/recover.py" >/dev/null 2>&1 &

# Project-local state pointer (hooks run from the project cwd).
state=".claude/state.md"
if [ -f "$state" ]; then
  lines=$(wc -l < "$state" 2>/dev/null | tr -d ' ')
  upd=$(sed -n 's/^# STATE.*updated \([0-9-]*\).*/\1/p' "$state" | head -1)
  printf '\nstate.md exists (%s lines, updated %s) — read it before exploring the codebase.\n' "$lines" "${upd:-?}"
fi
exit 0
