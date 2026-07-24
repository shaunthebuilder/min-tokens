#!/usr/bin/env bash
# min-tokens SessionStart: inject rules unless kill-switch present; note project state.md. Always exit 0.
off="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-off"
[ -f "$off" ] && exit 0

dir="$(cd "$(dirname "$0")" && pwd)"
cat "$dir/rules.md" 2>/dev/null

# Project-local state pointer (hooks run from the project cwd).
state=".claude/state.md"
if [ -f "$state" ]; then
  lines=$(wc -l < "$state" 2>/dev/null | tr -d ' ')
  upd=$(sed -n 's/^# STATE.*updated \([0-9-]*\).*/\1/p' "$state" | head -1)
  printf '\nstate.md exists (%s lines, updated %s) — read it before exploring the codebase.\n' "$lines" "${upd:-?}"
fi
exit 0
