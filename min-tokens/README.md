# min-tokens

A Claude Code plugin that stretches the $20 Pro plan **without any regression in reasoning, planning, creativity, or deliverable quality**. One always-on rules block, context-ceiling warnings, and a single `/min-tokens` control surface. Absorbs and replaces the ponytail plugin.

## Download

[min-tokens-4.0.zip](../min-tokens-4.0.zip) — current build (v4.0).

## What it does (zero touch)

- **Session-start rules** — every new thread, the plugin injects a ~1.5K-token block (see `hooks/rules.md`): the cost model itself (what a token in context actually costs, so the model can reason rather than pattern-match), then 11 rules — two output registers, surgical reads, capped command and search output, batching, screenshot discipline, no casual subagents/web-fetches, the state.md protocol, model-fit habits, ponytail's build-lazy ladder, and the never-economize guardrail. Injected once and cached; nothing to invoke.
- **The response contract** — a second block (`hooks/style.md`) governing how replies are *written*, not how tokens are spent: answer first, only what changes your next move, one kind of content per block, plain wording with precision kept, and every fact carrying its consequence. Three worked before/after examples ship with it, because an exemplar outperforms a list of clauses. A 90-word reminder of the three laws that decay (answer first, cut what changes nothing, say what it changes) is re-injected on every prompt, so the contract sits next to your message instead of 60K tokens behind it. Compliance is logged silently to `~/.claude/min-tokens-style.log` — one line per reply with its word count and whether line one stated the verdict — so you can tell in a day whether it landed. Measure it with `scripts/style-ab.py`.
- **Large-result notices** — a `PostToolUse` hook reports what an oversized tool result cost (`~32K tokens to context permanently`), at most 3× per session, above ~10K tokens. A number, never a directive: a hook that discourages reading is a hook that degrades answers.
- **Context gate** — a `UserPromptSubmit` hook estimates live context from the transcript. Silent below 95K. At ≥95K it holds **every** message and shows you three one-word choices: `go` (continue — costs zero tokens, since the hook intercepts before the model runs), `save` (update `.claude/state.md`, then answer), `new` (save + hand the message to a fresh thread). Your message is stashed and re-injected, so nothing is ever retyped. At ≥125K, `go` also forces a one-time state.md save so an abandoned thread stays recoverable; at ≥150K the wording escalates but `go` is never taken away. It fires on every message because it costs nothing — a warning you have to be lucky to see is not a warning.
- **State recovery for abandoned threads** — if a thread ends without a save, the next session in that project rebuilds `.claude/state.md` from the old transcript **out of band**: a detached `claude -p` call (Sonnet, medium effort, Haiku fallback) reads the previous transcript filtered to human prose only — user prompts and assistant text, no tool calls, no tool results, no thinking, which is a ~99% byte cut — and rewrites the file on disk. Not one token enters the new thread's context; it reads the refreshed file the ordinary way. Because the rewrite lands tens of seconds in, after the session already read the old copy, the next prompt carries a one-line "state.md was rewritten — re-read it". Costs ~$0.06 and should fire rarely, because it skips itself whenever the previous thread's work is already captured. That check is made from the transcript itself: prose written before the session's last `state.md` write is dropped, so a thread that saved at the end leaves nothing unsaved and is skipped, and a thread that saved then kept working has only its unsaved tail summarized. What remains must still clear a real work threshold (20 prose blocks / 15K chars) — a barely-started thread never triggers a rewrite of an accumulated state file. A transcript touched in the last 5 minutes is treated as still open and deferred to a later session start, never summarized mid-flight, and only `startup`/`clear` sessions recover at all — never `compact` or `resume`, which happen mid-thread. Recovery holds an exclusive per-project lock, so two sessions opened moments apart can't both rewrite the file and silently discard each other's work. Requires a `.claude/state.md` to already exist — the plugin never creates one in a project that doesn't use it — and never overwrites a good file with a reply that is malformed, or that sheds more than 25% of what it replaces; the three most recent timestamped `state.md.<ts>.bak` copies are kept either way. This is also where consolidation happens: the full rewrite runs off-context, where it's cheapest.
- **`/min-tokens`** — status (context size + this week's weighted usage by model + the single highest-value action). Answered by the hook with **zero model tokens** — the prompt never reaches the model.
- **`/save`** (or **`/min-tokens save`**) — write/refresh `.claude/state.md` so the next session reads ~1–2K of state instead of re-exploring the codebase. Then start a new thread — `/clear` if you're in the CLI, never `/compact`. The one subcommand that runs the model (it summarizes the conversation). `/save` works while the context gate is holding a prompt, and releases that prompt afterwards — the gate never eats it.
- **`/min-tokens debt`** — the ledger of deliberate shortcuts. Rule 10 has the model mark each one with a `min:` comment naming its ceiling and upgrade path; without a way to read them back the convention is decorative — markers scattered across files nobody greps, and "upgrade later" quietly becomes never. This walks the project, groups every `min:` (and legacy `ponytail:`) marker by file, and prints each one verbatim. Answered by the hook with **zero model tokens** — it is a grep-and-group, so the harness does it and the model never runs.
- **`/min-tokens off` / `on`** — kill-switch via `~/.claude/.min-tokens-off`; also answered by the hook with zero model tokens.

The only thing that can't be automated is starting the new thread when warned. A new thread and `/clear` cost exactly the same — both begin a fresh context that reads `state.md` back — but the new thread leaves the old conversation scrollable, so prefer it wherever the UI has threads.

## Install (local)

```bash
claude plugin marketplace add /Users/shantanu.rastogi/Development/Everything/min-tokens
claude plugin install min-tokens@min-tokens
claude plugin disable ponytail@ponytail   # ponytail is absorbed into rule 10; disable (reversible) or uninstall
```
Restart / start a new session to activate. Verify: a fresh session shows the rules block; `/min-tokens` reports usage.

## Recommended settings (apply yourself; not set silently)

```jsonc
{
  "env": { "BASH_MAX_OUTPUT_LENGTH": "20000" },  // caps runaway command output
  "effortLevel": "low"                            // daily default; raise per-task for planning
  // do NOT set MAX_THINKING_TOKENS — thinking depth is a quality guardrail
}
```

## Model routing (habit, not automation)

| Work | Model | Effort |
|---|---|---|
| Architecture, planning, hard debugging, evals design | Opus / Fable | high |
| Executing a clear plan; refactors; tests; CRUD; docs from an outline | **Sonnet (default)** | medium/low |
| Mechanical edits, renames, log parsing, format conversion | Haiku | low |

Sonnet is the default; escalate deliberately and de-escalate the moment planning ends. Switch only at task boundaries — the prompt cache is per-model, so a mid-task switch re-pays the whole context as a cache write.

## Thresholds

Override via env: `MIN_TOKENS_SOFT` (gate threshold, default 95000), `MIN_TOKENS_AUTO` (forced-save threshold, default 125000), `MIN_TOKENS_HARD` (default 150000).

State recovery: `MIN_TOKENS_RECOVER_MODEL` (default `sonnet`), `MIN_TOKENS_RECOVER_EFFORT` (default `medium`), `MIN_TOKENS_RECOVER_FALLBACK` (default `haiku`), `MIN_TOKENS_RECOVER_BUDGET` (dollars, default `0.50`), `MIN_TOKENS_RECOVER_TIMEOUT` (seconds, default `300`), `MIN_TOKENS_RECOVER_MAX_CHARS` (default 400000), `MIN_TOKENS_RECOVER_QUIET_S` (seconds a transcript must be idle before it counts as closed, default `300`), `MIN_TOKENS_RECOVER_MIN_BLOCKS` / `MIN_TOKENS_RECOVER_MIN_CHARS` (unsaved work required to justify a rewrite, defaults `20` / `15000`), `MIN_TOKENS_RECOVER_MIN_KEEP` (fraction of the old file the rewrite must retain, default `0.75`), `MIN_TOKENS_RECOVER_BAK_KEEP` (backups retained, default `3`). Set `MIN_TOKENS_NO_RECOVER=1` to disable recovery while keeping everything else. Log: `~/.claude/.min-tokens-warned/recover.log`.

## Guardrails — never economized

Thinking/planning depth, correctness checks, security, input validation, accessibility, deliverable completeness, and any explanation the user asked for. Token economy applies only to process chatter, redundant context, oversized tool results, model overkill, and session lifecycle.
