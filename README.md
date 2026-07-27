# min-tokens

Token economy for [Claude Code](https://claude.com/claude-code) — **stretch a fixed-price plan 3–5× with zero regression in reasoning, planning, creativity, or deliverable quality.**

One always-on rules block, context-ceiling warnings, and a single `/min-tokens` control surface. Absorbs and replaces the old `ponytail` plugin.

- **[Whitepaper](whitepaper.html)** — intent, features, the v1→v2 evolution, and how to use it well.
- **[Quickstart](quickstart.html)** — non-technical install-and-go guide.
- **[min-tokens.zip](min-tokens.zip)** — the plugin as a standalone download (identical to `min-tokens/`).

## What it does (zero touch)

- **Session-start rules** — every new thread, a ~1.1K-token block (`min-tokens/hooks/rules.md`) is injected: the cost model itself (what a token in context actually costs, so the model can reason rather than pattern-match), then 11 rules — two output registers, surgical reads, capped command and search output, batching, screenshot discipline, no casual subagents/web-fetches, the `state.md` protocol, model-fit habits, a build-lazy ladder, and a never-economize guardrail. Injected once and cached; nothing to invoke.
- **Large-result notices** — a `PostToolUse` hook reports what an oversized tool result cost (`~32K tokens to context permanently`), at most 3× per session, above ~10K tokens. A number, never a directive: a hook that discourages reading is a hook that degrades answers.
- **Context-ceiling warnings** — a `UserPromptSubmit` hook estimates live context from the transcript and prints a one-line warning only at ≥80K (soft) and ≥120K (hard) tokens. Silent otherwise.
- **`/min-tokens`** — status (context size, this week's weighted usage by model, the single highest-value action). Answered by the hook with **zero model tokens**.
- **`/min-tokens save`** — writes/refreshes `.claude/state.md` so the next session reads ~1–2K tokens of state instead of re-exploring the codebase. Then `/clear` (never `/compact`). The one subcommand that spends a model turn — it summarizes the conversation.
- **`/min-tokens off` / `on`** — kill switch, also answered by the hook for free.

The only thing that can't be automated is typing `/clear` when warned.

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

Override via env: `MIN_TOKENS_SOFT` (default 80000), `MIN_TOKENS_HARD` (default 120000).

## Guardrails — never economized

Thinking/planning depth, correctness checks, security, input validation, accessibility, deliverable completeness, and any explanation the user asked for. Token economy applies only to process chatter, redundant context, oversized tool results, model overkill, and session lifecycle.

## License

[MIT](LICENSE) — open source, use and modify freely.
