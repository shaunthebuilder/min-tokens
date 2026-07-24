---
name: min-tokens
description: Token-usage status and session-state control for the min-tokens plugin. Use ONLY when the user explicitly types /min-tokens (optionally followed by save, off, or on). Never auto-invoke — the always-on rules are already injected by the plugin's session-start hook.
---

# min-tokens

The token-economy rules are injected automatically at every session start by the plugin's hook — this skill is *only* the explicit `/min-tokens` control surface. `/min-tokens` (status), `/min-tokens off`, and `/min-tokens on` are normally answered by the `context-watch.py` hook with **zero model tokens** — you should rarely see them here. `/min-tokens save` is the one subcommand that needs you (it summarizes this conversation). Read the argument and run the one matching subcommand. Keep every response ≤10 lines.

## `/min-tokens save` — persist state, prep for /clear (primary)
Write or refresh `.claude/state.md` in the current project (create the `.claude/` dir if missing) from THIS conversation, using the schema below. Hard cap 150 lines — if over, trim the oldest/superseded Decisions first. Write it in **compressed register**: telegraphic prose, structure preserved, and never alter anything inside backticks, file paths, or commands. Confirm in one line, then tell the user to `/clear`. This is the cheap context-rebuild: the next session reads ~1–2K of state instead of re-exploring 20–50K.

## state.md schema (for `save`)
```markdown
# STATE — <project> (updated YYYY-MM-DD)
## Goal
<1–3 lines: what we're building and why>
## Now
<current task, exact next action>
## Decisions
<bullets: decision + one-line why; newest first; prune superseded>
## Map
<only the files that matter: path — one-line role>
## Constraints & gotchas
<things that bit us; API quirks; env facts>
## Next
<ordered short list>
```

## executable plan artifact (for model handoff)
Rule 9 requires any plan that a cheaper model (or a fresh thread) will execute to be an *executable artifact*, not a discussion. Write the reasoning INTO each step so the executor never has to re-derive it. Save to `plan.md` and point `state.md` `## Now`/`## Next` at it before `save`.

Discursive (bad — forces re-reasoning): "We should probably update the auth middleware to handle the new token format, then make sure the tests still pass."

Executable (good):
```markdown
## Step 1 — accept new JWT `kid` header
- File: src/auth/middleware.ts:42 (`verifyToken`)
- Change: read `header.kid`; if absent, fall back to default key (current behavior)
- Why: rotated keys now stamp `kid`; missing = legacy token, must still verify
- Accept: `npm test -- auth/middleware.test.ts` green; add case for absent `kid`
## Step 2 — ...
```
Each step is self-contained: exact path/symbol, the change, the reason, and a check the executor can run without judgement calls.

## Fallback: status / off / on (only if the hook is disabled)
The hook handles these for free; do this only if it didn't fire.
- **status** — run both in one Bash block, then report context size & % of ceiling, this week's weighted usage by model, and the single highest-value action ("switch to Sonnet", "save + clear", or "nothing — efficient"). Scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/` (fallback: `~/.claude/plugins/cache/min-tokens*/`):
  ```
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/context-size.py"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage-report.py" --days 7
  ```
- **off**: `touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-off"` (stops next session's injection; stop applying the rules now).
- **on**: `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-off"`.
