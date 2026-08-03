#!/usr/bin/env python3
"""min-tokens UserPromptSubmit hook.

Two jobs, both fail-silent (exit 0 — a hook must never block a session):
  1. Answer /min-tokens, /min-tokens off, /min-tokens on locally and BLOCK the
     prompt (decision:"block") so the model never runs — zero model tokens.
     /min-tokens save passes through: it needs the model to summarize the convo.
  2. THE GATE. Once context >= GATE_AT, block EVERY prompt (decision:"block")
     and show the user three one-word choices: go / save / new. The blocked
     prompt is stashed and re-injected via additionalContext on the next turn,
     so nothing is ever retyped and `go` costs literally zero model tokens.
  3. /save (and /min-tokens save) is answered here too, but as additionalContext
     rather than a block: the model still has to write state.md. It is handled
     BEFORE the gate so the shortcut keeps working while the gate is holding
     prompts — which is when the user is most likely to reach for it.

     Why a gate and not the old AskUserQuestion ladder (do not re-add the
     ladder — see state.md):
       - Plain stdout from this hook is additionalContext: it reaches the MODEL,
         not the user, and the model may silently not relay it. Only
         decision:"block" renders to the user. A warning the user never sees is
         not a warning. Measured: session 6d58f1be printed its hard-ceiling
         warning and ran on to 404K anyway.
       - Sampling happens once per USER PROMPT. The same session jumped 61K ->
         390K inside ONE turn. Any design whose fire condition is a WINDOW
         (75K-100K) can be skipped entirely. A gate is a FLOOR: a sampling gap
         makes it fire LATE, never never.
       - AskUserQuestion costs ~15K token-equivalents per fire (two model turns
         re-reading the whole context) EVEN IF the user declines. The gate costs
         zero, which is the only reason it can afford to fire every message.
     GATE_AT/AUTO_AT/HARD only change the gate's wording and urgency; the gate
     itself is unconditional above GATE_AT. At >= AUTO_AT, choosing `go` also
     fires the one-shot save backstop so state.md is written anyway.

Command interception mirrors caveman-stats: match the RAW typed slash text
(including the plugin-namespaced `/min-tokens:min-tokens` form). If Claude Code
expands the skill before this hook sees the prompt, the match simply misses and
the SKILL.md answers instead — same behavior as before, never worse.
"""
import sys, os, re, json, subprocess, time

GATE_AT = int(os.environ.get("MIN_TOKENS_SOFT", "95000"))
AUTO_AT = int(os.environ.get("MIN_TOKENS_AUTO", "125000"))
HARD = int(os.environ.get("MIN_TOKENS_HARD", "150000"))
STATE_CAP = int(os.environ.get("MIN_TOKENS_STATE_CAP", "20000"))

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
OFF_FLAG = os.path.join(CONFIG_DIR, ".min-tokens-off")
WARN_DIR = os.path.join(CONFIG_DIR, ".min-tokens-warned")

# /min-tokens [arg]  or  /min-tokens:min-tokens [arg]
CMD_RE = re.compile(r"^/min-tokens(?::min-tokens)?(?:\s+(\S+))?\s*$", re.IGNORECASE)

# `/save` is the shortcut for `/min-tokens save`; both land in handle_save().
# /save, the namespaced command form /min-tokens:save, and /min-tokens save.
SAVE_RE = re.compile(r"^/(?:save|min-tokens:save|min-tokens(?::min-tokens)?\s+save)\s*$",
                     re.IGNORECASE)
# commands/save.md carries this literal, so we still recognize the request when
# Claude Code expands the command file before this hook sees the prompt.
SAVE_MARK = "min-tokens:save-command"


def block(reason):
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason.strip()}))


def status_reason(transcript_path):
    scripts = os.path.join(PLUGIN_ROOT, "scripts")
    out = []
    for name, extra in (("context-size.py", [transcript_path] if transcript_path else []), ("usage-report.py", ["--days", "7"])):
        p = os.path.join(scripts, name)
        try:
            r = subprocess.run([sys.executable, p, *extra], capture_output=True,
                               text=True, timeout=5)
            out.append((r.stdout or r.stderr).strip())
        except Exception:
            out.append(f"({name} unavailable)")
    # The state.md budget has never been visible to anyone — not the user, and not
    # the model, which was being asked to enforce a cap on a number it never saw.
    # Zero model tokens: this whole block is computed in the hook.
    try:
        n = os.path.getsize(os.path.join(os.getcwd(), ".claude", "state.md"))
        out.append(f"state.md: {n/1000:.1f}K chars ({100*n//STATE_CAP}% of the "
                   f"{STATE_CAP//1000}K cap)" + ("  — consolidation due" if n > STATE_CAP else ""))
    except OSError:
        pass
    # Off-context recovery logs its rejections and nothing ever reads the log. A
    # marker-split bug burned a real `claude -p` call on every session start for a
    # day, seen by no one. Surfacing the last rejection here costs zero tokens.
    try:
        with open(os.path.join(WARN_DIR, "recover.log")) as f:
            bad = [l.strip() for l in f.read().splitlines()[-40:] if "REJECTED" in l]
        if bad:
            out.append("state recovery is failing — " + bad[-1])
    except OSError:
        pass
    out.append("Highest-value action: if context is near or over the ceiling, run /min-tokens save "
               "then start a new thread (/clear in the CLI); if an expensive model is doing routine "
               "work, switch to Sonnet; else nothing.")
    return "\n".join(x for x in out if x)


def debt_reason():
    """The `min:` marker ledger. Mechanical grep-and-group, so the hook does it
    and the model never runs — Rule 10's markers become readable for 0 tokens."""
    try:
        r = subprocess.run([sys.executable, os.path.join(PLUGIN_ROOT, "scripts", "debt.py"),
                            os.getcwd()], capture_output=True, text=True, timeout=15)
        return (r.stdout or r.stderr).strip() or "debt ledger: no output."
    except Exception:
        return "debt ledger unavailable (scripts/debt.py did not run)."


def handle_command(arg, transcript_path):
    """Return True if this prompt was a min-tokens command we fully handled."""
    if arg is None:
        block(status_reason(transcript_path))
    elif arg.lower() == "debt":
        block(debt_reason())
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


def _prune():
    """Drop markers older than 7d. Never raises."""
    try:
        os.makedirs(WARN_DIR, exist_ok=True)
        now = time.time()
        for f in os.listdir(WARN_DIR):
            p = os.path.join(WARN_DIR, f)
            if now - os.path.getmtime(p) > 604800:
                os.remove(p)
    except Exception:
        pass


def recovered_notice(session_id):
    """One line, once, if recover.py rewrote state.md after this session started.

    SessionStart reads state.md in ~5s; the detached recovery lands tens of
    seconds later, so without this the thread runs on the stale copy it already
    read and the rewrite is invisible to it.
    """
    try:
        flag = os.path.join(WARN_DIR, f"{session_id or 'unknown'}-recovered")
        if not os.path.exists(flag):
            return
        os.remove(flag)
        print(".claude/state.md was rewritten from the previous thread after this session started — re-read it before relying on it.")
    except Exception:
        pass


def _gate_path(session_id):
    return os.path.join(WARN_DIR, f"{session_id or 'unknown'}-gate.json")


def load_stash(session_id):
    """The prompt held by the last gate fire, or None. Never raises."""
    try:
        with open(_gate_path(session_id)) as f:
            return json.load(f).get("prompt") or None
    except Exception:
        return None


def save_stash(session_id, prompt):
    """Hold the blocked prompt so the user never retypes it. Never raises."""
    _prune()
    try:
        os.makedirs(WARN_DIR, exist_ok=True)
        with open(_gate_path(session_id), "w") as f:
            json.dump({"prompt": prompt}, f)
    except Exception:
        pass


def clear_stash(session_id):
    try:
        os.remove(_gate_path(session_id))
    except Exception:
        pass


def warn_once(session_id, level):
    """True the first time this session crosses this level; False after. Never raises."""
    try:
        _prune()
        marker = os.path.join(WARN_DIR, f"{session_id or 'unknown'}-{level}")
        if os.path.exists(marker):
            return False
        open(marker, "w").close()
        return True
    except Exception:
        return True  # on any failure, warn — a missed warning is worse than a repeat


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


# The save instruction is spelled out here, not left to the model, because an
# unguided save turn is the single most expensive thing this plugin can trigger:
# re-emitting a 30KB state.md is ~9K OUTPUT tokens, billed 5-10x input. Patching
# costs a fraction of that and keeps the file just as useful.
SAVE_HOWTO = ("Run the /min-tokens save procedure (skills/min-tokens/SKILL.md): run "
              "`wc -c .claude/state.md`, then PATCH the file with Edit calls touching only the "
              "sections that changed (you already have it in context — do not re-read it, and "
              "never re-emit it whole). If it is over 20000 chars, do NOT consolidate here — "
              "patch anyway and drop the consolidate flag per SKILL.md so the off-context "
              "consolidator does it for free. Confirm in ONE line: no summary of what you "
              "wrote, no file echo, no commentary.")


def is_save_request(prompt):
    return bool(SAVE_RE.match(prompt)) or SAVE_MARK in prompt


def handle_save(sid, ctx):
    """`/save` — shortcut for `/min-tokens save`, and a first-class gate answer.

    The gate holds EVERY prompt past the ceiling, which is exactly when a user is
    most likely to type /save — without this it would hold /save itself and reply
    with the gate dialog, i.e. the shortcut would stop working precisely when it
    matters. Handled here, /save means the same thing gated or not: save now,
    then deal with whatever was held. It is `save` plus "there may be nothing
    held", so it reuses that branch's stash + backstop bookkeeping exactly.
    """
    stash = load_stash(sid)
    clear_stash(sid)
    if ctx >= AUTO_AT:
        warn_once(sid, "auto")  # a real save happened; don't also fire the backstop
    out = "[/save] " + SAVE_HOWTO
    if stash:
        out += (" THEN answer the message the context gate held, which follows below — "
                "respond to THAT, not to the word \"/save\", and do not mention the gate."
                "\n\n" + stash)
    else:
        out += (" Then tell them they can start a new thread if they want a fresh "
                "context (/clear in the CLI) — never /compact.")
    print(out)


def gate_reason(k, ctx):
    """User-visible gate text. The user reads this on EVERY message past the
    ceiling, so it has to be scannable in one glance.

    The dialog renders plain text in a narrow wrapped box and collapses all
    whitespace \u2014 markdown prints literally, newlines/bullets/indent vanish
    (verified on screen). Emoji are the one glyph that survives collapse and
    still separates the three choices, so each option is anchored by its own
    emoji; the newlines are kept in case a future/other client honors them.
    """
    if ctx >= HARD:
        head = f"\u26d4 Context ~{k}K \u2014 OVER the {HARD // 1000}K hard ceiling."
        rec = "new"
    elif ctx >= AUTO_AT:
        head = (f"\u26a0\ufe0f Context ~{k}K \u2014 past {AUTO_AT // 1000}K, "
                "where go also forces a state.md save.")
        rec = "new"
    else:
        head = f"\u26a0\ufe0f Context ~{k}K \u2014 past the {GATE_AT // 1000}K ceiling."
        rec = "save"
    return (f"{head}\nYour prompt is held. Reply with one word:\n"
            "\u25b6\ufe0f go \u2014 continue here, costs 0 tokens\n"
            "\U0001f4be save \u2014 update .claude/state.md, then answer it\n"
            "\U0001f195 new \u2014 save, then hand it to a new thread\n"
            f"\U0001f449 Suggested: {rec}.")

def gate(sid, prompt, ctx, k):
    """The context gate. Returns nothing; either blocks or emits additionalContext."""
    stash = load_stash(sid)
    answer = (prompt or "").strip().lower()

    if stash and answer in ("go", "save", "new"):
        clear_stash(sid)
        if answer == "go":
            out = ("[context gate] The user chose to continue. Their real message was held by the "
                   "gate and follows below — respond to THAT, not to the word \"go\". Do not "
                   "mention the gate.\n\n" + stash)
            # The AUTO_AT backstop: continuing past AUTO_AT still writes state.md, so an
            # abandoned thread is recoverable. Verified worthwhile: recover.py feeds the
            # summarizer the existing state.md PLUS only the post-save tail, so a later
            # rewrite appends the unsaved work rather than replacing it with a stale file.
            if ctx >= AUTO_AT and warn_once(sid, "auto"):
                out += ("\n\n[min-tokens backstop] Context is past " + str(AUTO_AT // 1000) +
                        "K with no save this session. Before ending this turn, also: " + SAVE_HOWTO +
                        " Do not ask first — just do it.")
            print(out)
        elif answer == "save":
            warn_once(sid, "auto")  # a real save happened; don't also fire the backstop
            print("[context gate] The user chose to save. FIRST: " + SAVE_HOWTO +
                  " THEN answer their held message, which follows below.\n\n" + stash)
        else:  # new
            warn_once(sid, "auto")
            # `## Held` is a SINGLE SLOT, overwritten every time — not an append.
            # Appending leaked one dead line per handoff (three accumulated here,
            # one of them the string "hi"), and under move-not-shorten a stale
            # line is never removed, only relocated.
            print("[context gate] The user chose to save and hand off. " + SAVE_HOWTO +
                  " Record their held message VERBATIM in state.md under a `## Held` section, "
                  "REPLACING whatever that section already contains (it is one slot, not a "
                  "list), so the next thread picks it up and clears it once acted on. Do NOT "
                  "answer it here. Then tell them to start a new thread.\n\nHeld message:\n" + stash)
        return

    # Not an answer (or nothing held): hold this prompt and gate.
    save_stash(sid, prompt or "")
    block(gate_reason(k, ctx))


def read_ctx(tp):
    """Current context size in tokens, or 0 if it can't be read. Never raises."""
    try:
        if tp and os.path.exists(tp):
            u = last_assistant_usage(tp)
            if u:
                return (u.get("input_tokens", 0)
                        + u.get("cache_read_input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0))
    except Exception:
        pass
    return 0


def main():
    data = json.loads(sys.stdin.read().lstrip("﻿"))
    tp = data.get("transcript_path")
    sid = data.get("session_id")
    prompt = (data.get("prompt") or "").strip()

    m = CMD_RE.match(prompt)
    if m and handle_command(m.group(1), tp):
        return  # command handled + blocked; skip the ceiling warning

    # Before the OFF check and before the gate: an explicit save is always honored.
    if is_save_request(prompt):
        handle_save(sid, read_ctx(tp))
        return

    if os.path.exists(OFF_FLAG):
        return

    recovered_notice(sid)

    ctx = read_ctx(tp)
    if not ctx:
        return
    k = ctx // 1000

    if ctx < GATE_AT:
        clear_stash(sid)  # dropped back under the ceiling (rare); don't strand a held prompt
        return

    gate(sid, prompt, ctx, k)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a hook must never block a session
