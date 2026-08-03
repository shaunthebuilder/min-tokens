---
name: min-tokens
description: Token-usage status and session-state control for the min-tokens plugin. Use ONLY when the user explicitly types /min-tokens (optionally followed by save, debt, off, or on) or the /save shortcut. Never auto-invoke — the always-on rules are already injected by the plugin's session-start hook.
---

# min-tokens

The token-economy rules are injected automatically at every session start by the plugin's hook — this skill is *only* the explicit `/min-tokens` control surface. `/min-tokens` (status), `/min-tokens debt`, `/min-tokens off`, and `/min-tokens on` are normally answered by the `context-watch.py` hook with **zero model tokens** — you should rarely see them here. `/min-tokens save` is the one subcommand that needs you (it summarizes this conversation). Read the argument and run the one matching subcommand. Keep every response ≤10 lines.

## `/min-tokens save` (shortcut: `/save`) — persist state, prep for a new thread (primary)
`/save` is the same command. Both are normally answered by `context-watch.py`, which injects the procedure below directly — including while the context gate is holding a prompt, so the shortcut keeps working past the ceiling. If that happened you already have the instructions and should NOT read this file.

Write or refresh `.claude/state.md` in the current project (create the `.claude/` dir if missing) from THIS conversation, using the schema below. Write it in **compressed register**: telegraphic prose, structure preserved, and never alter anything inside backticks, file paths, or commands.

**The save turn must be cheap — an unguided one is the most expensive thing this plugin can trigger.** Re-emitting a 30KB state.md is ~9K output tokens billed at 5–10× input; patching the same update costs a fraction and produces the identical file.
- **Step 1 is always `wc -c .claude/state.md`** (~20 tokens). You are enforcing a character budget; you cannot enforce it on a file whose size you never look at, and guessing "it's probably fine" is why the cap went unenforced for its first days of life.
- **PATCH, don't re-emit.** If state.md exists it is already in your context from session start — do not re-read it, and update it with `Edit` calls touching only the sections that actually changed. A full `Write` is for a file that doesn't exist yet.
- **Confirm in ONE line.** No summary of what you wrote, no diff, no file echo, no "here's what I captured". The user can read the file. Then tell them to start a new thread (`/clear` if they're in the CLI) — never `/compact`.

**Size cap: 20,000 characters** (`wc -c .claude/state.md`), not a line count — lines here run long and a line cap hides real bloat.

**Over cap, the save turn still just patches — it NEVER consolidates.** Consolidation is a full rewrite, ~9K output tokens, and this turn was almost certainly triggered by a context gate that fired *because* context is already expensive. Instead: patch as normal, then flag the file for the off-context consolidator, which runs in a separate `claude -p` call at the next session start and costs this thread nothing:
```bash
python3 -c "import hashlib,os;d=os.path.expanduser('${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-warned');os.makedirs(d,exist_ok=True);open(os.path.join(d,hashlib.sha1(os.path.abspath('.').encode()).hexdigest()[:12]+'-consolidate'),'w').close()"
```
Say so in the one-line confirmation (e.g. `saved — 24K chars, flagged for consolidation`).

**Consolidation MOVES, it never SHORTENS.** Whoever does it — you by hand at a cheap moment, or `recover.py` — the only legal operation is relocating bulk evidence verbatim into `.claude/notes/<YYYY-MM>-<topic>.md`, leaving behind ONE line that carries the **conclusion** plus the note path. Never paraphrase, merge, tidy or summarize existing text: repeated %-compression is exponential decay (survival `0.7^k`) and it has already destroyed content in this project. Acceptance check: `bytes(state.md) + bytes(notes/*) >= bytes(state.md before)`. Correcting a fact that is now false is always allowed and is not consolidation. `## Goal`, `## Now`, `## Next`, `## Constraints & gotchas`, `## Map` and every Decision recording a *rejected* approach stay resident at any size — a rejected approach must be refusable from state.md alone, without opening a note, or the pointer is a deletion that merely looks like preservation.

## state.md schema (for `save`)
```markdown
# STATE — <project> (updated YYYY-MM-DD)
## Goal
<1–3 lines: what we're building and why>
## Now
<current task, exact next action>
## Decisions
<bullets: decision + one-line why; newest first; prune superseded. Keep rejected approaches WITH their reasoning — they exist to stop the same bad idea being re-proposed>
## Map
<only the files that matter: path — one-line role>
## Constraints & gotchas
<things that bit us; API quirks; env facts>
## Held
<single slot: the message a context gate held, verbatim. OVERWRITE it, never append; clear it once acted on>
## Next
<ordered short list>
## Prior
<ONE section, newest superseded state only. On the next supersession its contents move to `.claude/notes/` — never deleted, never rewritten>
```
**These are the only sections.** Do not invent `## Prior (v3)`, `## Shipped`, `## Forensics` or similar: finished work with nowhere to go is exactly how a state file grows without bound (this one reached 16 sections against a 6-section schema, ~45% of its characters in archive sections nothing ever left). One `## Prior` slot, and `.claude/notes/` beyond it.

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

## Fallback: status / debt / off / on (only if the hook is disabled)
The hook handles these for free; do this only if it didn't fire.
- **status** — run both in one Bash block, then report context size & % of ceiling, this week's weighted usage by model, and the single highest-value action ("switch to Sonnet", "save + new thread", or "nothing — efficient"). Scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/` (fallback: `~/.claude/plugins/cache/min-tokens/*/*/scripts/`):
  ```
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/context-size.py"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/usage-report.py" --days 7
  ```
- **debt** — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/debt.py"`, then print its output verbatim. Do NOT paraphrase a shortcut's ceiling and do NOT start fixing them; this is a ledger, not a work order.
- **off**: `touch "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-off"` (stops next session's injection; stop applying the rules now).
- **on**: `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-off"`.
