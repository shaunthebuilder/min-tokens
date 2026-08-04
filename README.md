# min-tokens

Token economy for [Claude Code](https://claude.com/claude-code) — **stretch a fixed-price plan 3–5× with zero regression in reasoning, planning, creativity, or deliverable quality.**

One always-on rules block, a context gate, out-of-band state recovery, and a single `/min-tokens` control surface. Absorbs and replaces the old `ponytail` plugin.

- **[Whitepaper](whitepaper.html)** — intent, features, the v1→v2 evolution, and how to use it well.
- **[Quickstart](quickstart.html)** — non-technical install-and-go guide.
- **[min-tokens-4.0.zip](min-tokens-4.0.zip)** — the plugin as a standalone download (identical to `min-tokens/`).

## What it does (zero touch)

- **Session-start rules** — every new thread, a ~1.5K-token block (`min-tokens/hooks/rules.md`) is injected: the cost model itself (what a token in context actually costs, so the model can reason rather than pattern-match), then 11 rules — two output registers, surgical reads, capped command and search output, batching, screenshot discipline, no casual subagents/web-fetches, the `state.md` protocol, model-fit habits, a build-lazy ladder, and a never-economize guardrail. Injected once and cached; nothing to invoke.
- **The response contract** — a second always-on block (`min-tokens/hooks/style.md`) governing how replies are *written*, not how tokens are spent: answer first, only what changes your next move, one kind of content per block, plain wording with precision kept, and every fact carrying its consequence. Three worked before/after examples ship with it, because an exemplar outperforms a list of clauses. A 90-word reminder of the three laws that decay is re-injected on every prompt, so the contract sits next to your message instead of 60K tokens behind it. Compliance is logged silently to `~/.claude/min-tokens-style.log` — one line per reply, its word count and whether line one stated the verdict — so you can tell within a day whether it landed. Measure with `min-tokens/scripts/style-ab.py`.
- **Large-result notices** — a `PostToolUse` hook reports what an oversized tool result cost (`~32K tokens to context permanently`), at most 3× per session, above ~10K tokens. A number, never a directive: a hook that discourages reading is a hook that degrades answers.
- **Context gate** — a `UserPromptSubmit` hook estimates live context from the transcript. Silent below 95K. At ≥95K it holds **every** message and offers three one-word choices: `go` (continue — costs zero tokens, since the hook intercepts before the model runs), `save` (update `.claude/state.md`, then answer), `new` (save + hand the message to a fresh thread). Your message is stashed and re-injected, so nothing is retyped. At ≥125K, `go` also forces a one-time state.md save so an abandoned thread stays recoverable; at ≥150K the wording escalates but `go` is never taken away. It fires on every message because it costs nothing — a warning you have to be lucky to see is not a warning.
- **State recovery for abandoned threads** — if a thread ends without a save, the next session in that project rebuilds `.claude/state.md` from the old transcript **out of band**: a detached `claude -p` call (Sonnet, medium effort, Haiku fallback) reads the previous transcript filtered to human prose only — no tool calls, no tool results, no thinking, a ~99% byte cut — and rewrites the file on disk. Not one token enters the new thread's context. It skips itself whenever the previous thread's work was already saved, holds a per-project lock, keeps timestamped backups, and never replaces a good file with a malformed reply or one that sheds more than 25% of what it replaces. Full knob list in `min-tokens/README.md`.
- **`/min-tokens`** — status (context size, this week's weighted usage by model, the single highest-value action). Answered by the hook with **zero model tokens**.
- **`/save`** (or **`/min-tokens save`**) — writes/refreshes `.claude/state.md` so the next session reads ~1–2K tokens of state instead of re-exploring the codebase. Then start a new thread — `/clear` if you're in the CLI, never `/compact`. The one subcommand that spends a model turn, since it summarizes the conversation. It works while the gate is holding a prompt and releases that prompt afterwards.
- **`/min-tokens debt`** — the ledger of deliberate shortcuts. Rule 10 has the model mark each one with a `min:` comment naming its ceiling and upgrade path; this walks the project, groups every marker by file, and prints them verbatim. Answered by the hook with **zero model tokens** — it's a grep-and-group, so the harness does it.
- **`/min-tokens off` / `on`** — kill switch, also answered by the hook for free.

The only thing that can't be automated is starting the new thread when warned. A new thread and `/clear` cost exactly the same — both begin a fresh context that reads `state.md` back — but the new thread leaves the old conversation scrollable, so prefer it wherever the UI has threads.

## Why

An analysis of 265MB of real transcripts (112 sessions, ~16K assistant calls) found that ~97% of weighted spend went to the two most expensive model tiers for work a cheaper model handles identically, and cache-read re-processing of context alone accounted for ~47% of cost. min-tokens targets exactly those levers — model routing, context lifecycle, tool-result hygiene, turn-count, and chatter — and nothing else. See the [whitepaper](whitepaper.html) for the full measurement and the six guardrails it will never touch (thinking depth, deliverable completeness, correctness, security, accessibility, requested explanations).

## Install

```bash
claude plugin marketplace add /path/to/min-tokens
claude plugin install min-tokens@min-tokens
claude plugin disable ponytail@ponytail   # if installed — absorbed into rule 10
```

Restart or start a new session to activate. Verify: a fresh session shows the rules block; `/min-tokens` reports usage.

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

Override via env: `MIN_TOKENS_SOFT` (gate threshold, default 95000), `MIN_TOKENS_AUTO` (forced-save threshold, default 125000), `MIN_TOKENS_HARD` (default 150000). State recovery has its own `MIN_TOKENS_RECOVER_*` knobs — see `min-tokens/README.md`; `MIN_TOKENS_NO_RECOVER=1` disables recovery alone.

## Guardrails — never economized

Thinking/planning depth, correctness checks, security, input validation, accessibility, deliverable completeness, and any explanation the user asked for. Token economy applies only to process chatter, redundant context, oversized tool results, model overkill, and session lifecycle.

## License

[MIT](LICENSE) — open source, use and modify freely.
