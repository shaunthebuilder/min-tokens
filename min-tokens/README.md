# min-tokens

A Claude Code plugin that stretches the $20 Pro plan **without any regression in reasoning, planning, creativity, or deliverable quality**. One always-on rules block, context-ceiling warnings, and a single `/min-tokens` control surface. Absorbs and replaces the ponytail plugin.

## What it does (zero touch)

- **Session-start rules** — every new thread, the plugin injects an ~500-token, 11-rule block (see `hooks/rules.md`): two output registers, surgical reads, capped command output, batching, screenshot discipline, no casual subagents/web-fetches, the state.md protocol, model-fit habits, ponytail's build-lazy ladder, and the never-economize guardrail. Nothing to invoke.
- **Context-ceiling warnings** — a `UserPromptSubmit` hook estimates live context from the transcript and prints a one-line warning only at ≥80K (soft) and ≥120K (hard). Silent otherwise.
- **`/min-tokens`** — status (context size + this week's weighted usage by model + the single highest-value action). Answered by the hook with **zero model tokens** — the prompt never reaches the model.
- **`/min-tokens save`** — write/refresh `.claude/state.md` so the next session reads ~1–2K of state instead of re-exploring the codebase. Then `/clear` (never `/compact`). The one subcommand that runs the model (it summarizes the conversation).
- **`/min-tokens off` / `on`** — kill-switch via `~/.claude/.min-tokens-off`; also answered by the hook with zero model tokens.

The only thing that can't be automated is typing `/clear` when warned.

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

Override via env: `MIN_TOKENS_SOFT` (default 80000), `MIN_TOKENS_HARD` (default 120000).

## Guardrails — never economized

Thinking/planning depth, correctness checks, security, input validation, accessibility, deliverable completeness, and any explanation the user asked for. Token economy applies only to process chatter, redundant context, oversized tool results, model overkill, and session lifecycle.
