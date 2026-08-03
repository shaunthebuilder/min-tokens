#!/usr/bin/env python3
"""min-tokens out-of-band state recovery (Phase B).

Safety net for threads that were abandoned without a `/min-tokens save`.
Launched DETACHED from the SessionStart hook, so it can neither block the 5s
hook timeout nor put one token into the new thread's context: it summarizes the
PREVIOUS thread's transcript in a separate `claude -p` call and rewrites
`.claude/state.md` on disk. The new thread reads that file the normal way.

Why a separate call: the new thread has no memory of the old one, and reading a
prior transcript into live context would cost more than /compact in the one
place we keep clean. The filter below cuts a typical 8 MB transcript to ~53 KB
(~13K tokens) by dropping tool_result/tool_use/thinking blocks, which are ~99%
of the bytes and none of the reasoning.

Two modes, deliberately separated (they were one job, and that is what destroyed
data here on 2026-07-27):
  * RECOVER — append the previous thread's unsaved prose. Output shorter than the
    current state.md is rejected outright, so this path cannot lose content.
  * CONSOLIDATE — fires on SIZE alone (state.md > CAP) or on a marker dropped by
    an in-thread save. Its only legal operation is MOVE: bulk evidence relocates
    verbatim to `.claude/notes/`, each block leaving behind one line carrying its
    CONCLUSION plus the note path. Nothing is ever paraphrased or summarized out.
    A pointer whose conclusion is missing is a deletion that looks like
    preservation — you cannot know to open "why we rejected X" unless you already
    know X was rejected.

Every failure path is silent (exit 0) and leaves the existing state.md intact.
"""
import sys, os, io, re, json, glob, time, shutil, subprocess, fcntl, hashlib, datetime

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
OFF_FLAG = os.path.join(CONFIG_DIR, ".min-tokens-off")
WARN_DIR = os.path.join(CONFIG_DIR, ".min-tokens-warned")
LOG = os.path.join(WARN_DIR, "recover.log")

MODEL = os.environ.get("MIN_TOKENS_RECOVER_MODEL", "sonnet")
EFFORT = os.environ.get("MIN_TOKENS_RECOVER_EFFORT", "medium")
FALLBACK = os.environ.get("MIN_TOKENS_RECOVER_FALLBACK", "haiku")
BUDGET = os.environ.get("MIN_TOKENS_RECOVER_BUDGET", "0.50")
TIMEOUT = int(os.environ.get("MIN_TOKENS_RECOVER_TIMEOUT", "300"))
# Hard ceiling on what we feed the summarizer. Measured: a 7.7 MB transcript
# filters to ~53 KB, so this only bites on pathological threads; we then keep
# the TAIL, because recent turns are what state.md needs.
MAX_CHARS = int(os.environ.get("MIN_TOKENS_RECOVER_MAX_CHARS", "400000"))
# A transcript touched within this many seconds is assumed STILL OPEN. Recovering
# from a live thread summarizes half-finished work: observed 2026-07-27, two
# sessions each rewrote state.md from the same GROWING transcript (5 blocks/3KB,
# then 8 blocks/8KB, same file). Recovery is only DEFERRED, never dropped —
# state.md stays stale, so the next qualifying session start retries.
QUIET_S = int(os.environ.get("MIN_TOKENS_RECOVER_QUIET_S", "300"))
# Unsaved work must be substantial before we rewrite an accumulated state file.
# The old 4-block/500-char floor let 3KB of prose trigger a rewrite of 29KB.
MIN_BLOCKS = int(os.environ.get("MIN_TOKENS_RECOVER_MIN_BLOCKS", "20"))
MIN_CHARS = int(os.environ.get("MIN_TOKENS_RECOVER_MIN_CHARS", "15000"))
# Floor on a CONSOLIDATE pass, where state.md alone is expected to shrink. The
# real guard there is combined-bytes (state + note >= old state); this is the
# secondary sanity check on the resident half. Recovery does not use it at all:
# recovery is append-only, so its floor is exactly len(new) >= len(state).
MIN_KEEP = float(os.environ.get("MIN_TOKENS_RECOVER_MIN_KEEP", "0.25"))
# Size budget for state.md, mirrored from SKILL.md. Over it, consolidate mode
# fires on SIZE ALONE — independent of how much unsaved prose the last thread
# left. Without that, perfect save hygiene GUARANTEES unbounded growth: a thread
# that saved at the end leaves 0 unsaved blocks, recovery skips, and the file
# therefore never consolidates.
CAP = int(os.environ.get("MIN_TOKENS_STATE_CAP", "20000"))
BAK_KEEP = int(os.environ.get("MIN_TOKENS_RECOVER_BAK_KEEP", "3"))
# Rules injection is wanted on every SessionStart; recovery is not. On `compact`
# and `resume` we are MID-thread, and a rewrite from some other thread's
# transcript would land underneath live work.
SOURCES = {"startup", "clear"}

PROMPT = """You are updating a project state file for an AI coding assistant.

Below are (A) the current `.claude/state.md` and (B) the human-readable prose of \
the previous work session on this project, which ended without the state file \
being updated. Tool calls and their results were stripped; only user prompts and \
assistant prose remain, in order.

This is an APPEND, not a rewrite. Section A is already correct; your job is only \
to add what section B contains and A does not.

Rules:
- Output the COMPLETE new file and nothing else. No preamble, no code fence, no \
commentary. Your entire output is written to state.md verbatim.
- **Reproduce section A VERBATIM.** Every line of A must appear in your output, \
character for character. Do not merge, tidy, shorten, re-order or paraphrase any \
existing text, and do not drop anything — not even notes that look superseded. \
Your output must be LONGER than A. It is rejected outright if it is shorter.
- Your only freedoms are: (a) append new material from B into `## Now`, `## Next`, \
`## Decisions` and `## Constraints & gotchas`; (b) if B shows the work in `## Now` \
is finished and replaced, move the outgoing `## Now` text down into a `## Prior` \
section UNCHANGED and write the new state above it. That is a MOVE — the text \
survives word for word.
- Keep the `# STATE ... (updated YYYY-MM-DD)` header line, with today's date: \
{today}. Changing that date is the one exception to verbatim.
- Correcting a fact that section B proves is now FALSE is always permitted, and is \
not shortening: state the correction, keep the surrounding text.
- Reproduce file paths, commands, code, flags, IDs and numbers EXACTLY as they \
appear. Never paraphrase or "tidy" them. If a fact is not in the sources, do not \
invent it.
- Preserve decisions and rejected approaches with their reasoning — they exist to \
stop the same bad idea being re-proposed.
- Write new text in telegraphic, compressed prose. Structure over sentences. No filler.
- If the session ended mid-task, say so plainly in "Now", including what was \
verified and what was not.

=== A. CURRENT state.md ===
{state}

=== B. PREVIOUS SESSION (filtered) ===
{convo}
"""

# Consolidation is a SEPARATE job from recovery, and its only legal operation is
# MOVE. A %-target ("compress to the shortest protective form") is exponential
# decay: survival after k passes is 0.7^k, every pass looks locally reasonable,
# and it already cost us content here (finding F4 — a len/3 floor permitted 67%
# loss, firing every start, half-life ~3 threads, restored from .bak).
CONSOLIDATE_PROMPT = """You are consolidating a project state file for an AI coding assistant.

The file below has grown past its {cap}-character budget. Every thread on this \
project re-reads it, so size is a real recurring cost — but the detail in it is \
what stops solved problems being re-solved and bad ideas being re-proposed.

**The only operation you may perform is MOVE.** You never shorten, summarize, \
paraphrase, merge or delete any existing text. Bulk evidence relocates to an \
archive note; the conclusion it supports stays in state.md.

Output EXACTLY two documents, in this order, with these markers on their own lines:

=== STATE ===
<the complete new state.md>
=== NOTE ===
<the complete archive note>

Rules:
- Nothing else in your output. No preamble, no code fence, no commentary.
- Every character you remove from state.md must appear VERBATIM in the note. The \
two documents together must be at least as long as the input. Text is never \
rewritten on the way across.
- What MOVES: long derivations, forensic detail, measurement dumps, finished/shipped \
work, superseded `## Prior (...)` sections — the evidence.
- What STAYS, untouched: `## Goal`, `## Now`, `## Next`, `## Constraints & gotchas`, \
`## Decisions`, `## Map`, and every decision that records a REJECTED approach.
- **Each moved block leaves behind ONE line in state.md carrying its CONCLUSION plus \
the note path `{note}`.** Not "see the note" — the conclusion itself. A reader must be \
able to refuse a bad idea from state.md alone, without opening the note; the note is \
opened only to CHALLENGE a conclusion, never to discover one.
- Keep the `# STATE ... (updated YYYY-MM-DD)` header, date {today}.
- Reproduce file paths, commands, code, flags, IDs and numbers EXACTLY.
- Start the note with a `# ` heading naming the project and the archive date.

=== CURRENT state.md ({size} chars) ===
{state}
"""

# Headings whose disappearance means the consolidation went wrong.
PROTECTED = ("## Goal", "## Now", "## Next", "## Constraints & gotchas", "## Decisions")


def log(msg):
    try:
        os.makedirs(WARN_DIR, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _ts(s):
    """ISO-8601 Z timestamp -> epoch seconds. 0.0 if absent/unparseable."""
    try:
        return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return 0.0


def filter_transcript(path, state_mtime):
    """Human prose only: user prompts + assistant text blocks, in order.

    Drops tool_use/tool_result/thinking blocks and every non-conversation record
    type. tool_result blocks live inside `type:"user"` records, which is where
    ~98% of transcript bytes actually are.

    Prose written BEFORE the session's own last state.md write is discarded: it
    is already captured in the file we hand the summarizer as input A. This is
    what makes a correct save immune to being clobbered — a session that saved at
    the end leaves zero blocks and fails the threshold in main(). It also makes
    the save-then-keep-working case cheap and correct, summarizing only the tail
    that never made it into the file. mtime is checked so a state.md restored
    from a backup (older than the write it records) is not falsely treated as
    already containing that work.
    """
    out, cut, cut_ts = [], 0, 0.0
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            role = rec.get("type")
            if role not in ("user", "assistant"):
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, str):
                if content.strip():
                    out.append(f"[user] {content.strip()}")
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        t = (b.get("text") or "").strip()
                        if t:
                            out.append(f"[{role}] {t}")
                    elif b.get("type") == "tool_use":
                        fp = str((b.get("input") or {}).get("file_path") or "")
                        if os.path.basename(fp) == "state.md":
                            cut, cut_ts = len(out), _ts(rec.get("timestamp") or "")
    if cut and state_mtime >= cut_ts:
        out = out[cut:]
    text = "\n\n".join(out)
    if len(text) > MAX_CHARS:
        text = "[…earlier turns omitted…]\n\n" + text[-MAX_CHARS:]
    return text, len(out)


def prior_transcript(proj_dir, session_id):
    """Newest .jsonl in this project EXCLUDING the current session's own file.

    The live file is rewritten continuously from the moment this thread opens, so
    an unguarded "newest" is always the current one and the staleness check below
    would invert.
    """
    best, best_mtime, now = None, -1.0, time.time()
    for p in glob.glob(os.path.join(proj_dir, "*.jsonl")):
        if session_id and os.path.basename(p).startswith(session_id):
            continue
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if now - m < QUIET_S:
            continue  # still open, or another thread mid-turn — see QUIET_S
        if m > best_mtime:
            best, best_mtime = p, m
    return best, best_mtime


def acquire_lock(cwd):
    """Exclusive per-project lock, or None if another recovery holds it.

    Without this, two thread starts moments apart both read state.md, both spend
    ~2min in `claude -p`, and the slower write silently discards the faster one.
    Observed 2026-07-27: run A wrote 22122 chars at 15:37:51, run B — which had
    already read the pre-A file — overwrote it at 15:39:18, and B's .bak captured
    A's output, so neither the original nor A's result survived.
    """
    try:
        os.makedirs(WARN_DIR, exist_ok=True)
        key = hashlib.sha1(os.path.abspath(cwd).encode()).hexdigest()[:12]
        fd = open(os.path.join(WARN_DIR, f"recover-{key}.lock"), "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        return None


def rotate_bak(state_path):
    """Timestamped backup, newest BAK_KEEP retained.

    The old single `.bak` slot was overwritten by the very next recovery, so two
    runs in succession destroyed the last good copy.
    """
    shutil.copyfile(state_path, f"{state_path}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
    for p in sorted(glob.glob(f"{state_path}.*.bak"))[:-BAK_KEEP]:
        try:
            os.remove(p)
        except OSError:
            pass


def summarize(prompt):
    cmd = ["claude", "-p", "--model", MODEL, "--effort", EFFORT,
           "--fallback-model", FALLBACK, "--max-budget-usd", BUDGET]
    # RECURSION GUARD (observed for real, 2026-07-27): this child is itself a
    # Claude Code session, so it fires SessionStart, which launches recover.py
    # again. MIN_TOKENS_CHILD makes session-start.sh a no-op — it also stops the
    # rules block being injected into a call that has no use for it.
    env = dict(os.environ, MIN_TOKENS_CHILD="1")
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                       timeout=TIMEOUT, env=env)
    if r.returncode != 0:
        log(f"claude exited {r.returncode}: {(r.stderr or '').strip()[:300]}")
        return None
    return (r.stdout or "").strip()


def _key(cwd):
    return hashlib.sha1(os.path.abspath(cwd).encode()).hexdigest()[:12]


def consolidate_marker(cwd):
    """Path of the flag an in-thread save drops when state.md is over cap.

    The save turn deliberately does NOT consolidate: it is triggered by a gate
    that fired *because* context is expensive, so spending ~9K output tokens on
    a full rewrite there is exactly backwards. It patches, flags, and lets this
    off-context call do the rewrite.
    """
    return os.path.join(WARN_DIR, f"{_key(cwd)}-consolidate")


# Markers must be ALONE on their line. A substring split silently destroyed this
# feature: state.md documents consolidate mode and quotes both markers inline, so
# the split landed inside the reproduced state text — 5 chars of `state`, the whole
# file as `note`, "REJECTED: missing or malformed documents" on every session start
# for a day, each one a real ~100s `claude -p` call. Any state.md that documents
# this plugin is self-referential; assume it.
_STATE_MARK = re.compile(r"(?m)^=== STATE ===[ \t]*$")
_NOTE_MARK = re.compile(r"(?m)^=== NOTE ===[ \t]*$")


def split_docs(out):
    """(state, note) from the two-document reply, or (None, None)."""
    if not out:
        return None, None
    s = _STATE_MARK.search(out)
    start = s.end() if s else 0
    n = _NOTE_MARK.search(out, start)
    if not n:
        return None, None
    return out[start:n.start()].strip(), out[n.end():].strip()


def note_path(cwd):
    """A notes/ path that does not already exist.

    This process NEVER rewrites or deletes an existing note: the archive is
    append-only, which keeps the destructive surface exactly as narrow as it is
    today (one file, state.md, guarded by backups).
    """
    d = os.path.join(cwd, ".claude", "notes")
    base = os.path.join(d, time.strftime("%Y-%m") + "-archive")
    p = f"{base}.md"
    n = 2
    while os.path.exists(p):
        p = f"{base}-{n}.md"
        n += 1
    return p


def consolidate(state_path, state, cwd):
    """Move bulk evidence out of state.md into .claude/notes/. Move only."""
    npath = note_path(cwd)
    rel = os.path.relpath(npath, cwd)
    log(f"consolidating state.md ({len(state)} chars, cap {CAP}) -> {rel}")
    out = summarize(CONSOLIDATE_PROMPT.format(cap=CAP, today=time.strftime("%Y-%m-%d"),
                                              note=rel, size=len(state), state=state))
    new, note = split_docs(out)

    # Every guard fails toward NOT writing: a skipped consolidation is retried on
    # the next start, a bad one is unrecoverable.
    if not new or not note or not new.lstrip().startswith("# STATE"):
        log("REJECTED consolidation: missing or malformed documents — state.md left untouched")
        return
    if len(new) + len(note) < len(state):
        log(f"REJECTED consolidation: {len(new)}+{len(note)} chars < {len(state)} original "
            "(move-not-shorten violated) — state.md left untouched")
        return
    if len(new) < max(500, int(len(state) * MIN_KEEP)):
        log(f"REJECTED consolidation: resident half {len(new)} chars is implausibly small "
            f"vs {len(state)} — state.md left untouched")
        return
    missing = [h for h in PROTECTED if h in state and h not in new]
    if missing:
        log(f"REJECTED consolidation: protected heading(s) dropped {missing} — state.md left untouched")
        return
    gone = [h for h in re.findall(r"(?m)^## .*$", state) if h not in new]
    if gone and "notes/" not in new:
        log(f"REJECTED consolidation: {len(gone)} section(s) removed with no pointer to notes/ "
            "— state.md left untouched")
        return

    os.makedirs(os.path.dirname(npath), exist_ok=True)
    with open(npath, "w", encoding="utf-8") as f:
        f.write(note if note.endswith("\n") else note + "\n")
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new if new.endswith("\n") else new + "\n")
    rotate_bak(state_path)
    os.replace(tmp, state_path)
    try:
        os.remove(consolidate_marker(cwd))
    except OSError:
        pass
    log(f"state.md consolidated ({len(state)} -> {len(new)} chars, {len(note)} archived in {rel})")
    return True


def main():
    data = json.loads(sys.stdin.read().lstrip("﻿"))
    sid = data.get("session_id") or ""
    cwd = data.get("cwd") or os.getcwd()
    tp = data.get("transcript_path") or ""

    if (os.path.exists(OFF_FLAG) or os.environ.get("MIN_TOKENS_NO_RECOVER")
            or os.environ.get("MIN_TOKENS_CHILD")):
        return

    src = data.get("source")
    if src and src not in SOURCES:
        return  # compact/resume: mid-thread, see SOURCES

    # state.md is the opt-in: never create one in a project that doesn't use it.
    state_path = os.path.join(cwd, ".claude", "state.md")
    if not os.path.isfile(state_path):
        return

    lock = acquire_lock(cwd)
    if lock is None:
        log("skip: another recovery holds the lock for this project")
        return

    with open(state_path, encoding="utf-8") as f:
        state = f.read()

    wrote = recover(data, sid, tp, state_path, state)

    # Consolidation runs only when recovery did NOT, so unsaved prose is never
    # stranded: a rewritten state.md is newer than the prior transcript, which
    # would make every later start treat that thread as sealed. Recovery first,
    # consolidate on the next start.
    if not wrote and (os.path.exists(consolidate_marker(cwd)) or len(state) > CAP):
        wrote = consolidate(state_path, state, cwd)

    if wrote:
        # Ordering fix: this session already read the OLD state.md at startup.
        # Leave a flag so the next UserPromptSubmit tells it to re-read.
        try:
            os.makedirs(WARN_DIR, exist_ok=True)
            open(os.path.join(WARN_DIR, f"{sid or 'unknown'}-recovered"), "w").close()
        except Exception:
            pass


def recover(data, sid, tp, state_path, state):
    """Append the previous thread's unsaved prose. True if state.md was written."""
    proj_dir = os.path.dirname(tp)
    if not proj_dir or not os.path.isdir(proj_dir):
        return

    prior, prior_mtime = prior_transcript(proj_dir, sid)
    if not prior:
        return

    # The previous thread sealed its own state — nothing to recover, zero cost.
    if os.path.getmtime(state_path) >= prior_mtime:
        return

    convo, blocks = filter_transcript(prior, os.path.getmtime(state_path))
    # EITHER signal is enough. With `or` here, block count vetoed volume: a
    # tool-heavy thread leaving 2 prose blocks / 50395 chars was discarded three
    # times on 2026-07-28 as "not substantial". Skip only when both are small.
    if blocks < MIN_BLOCKS and len(convo) < MIN_CHARS:
        log(f"skip: {os.path.basename(prior)} leaves {blocks} unsaved prose "
            f"blocks / {len(convo)} chars (need {MIN_BLOCKS} blocks or {MIN_CHARS} chars)")
        return

    log(f"recovering from {os.path.basename(prior)} ({blocks} blocks, {len(convo)//1024}KB) via {MODEL}/{EFFORT}")
    new = summarize(PROMPT.format(today=time.strftime("%Y-%m-%d"),
                                  state=state, convo=convo))

    # Recovery is APPEND-ONLY, so the floor is exact rather than tuned: output
    # shorter than the input cannot be an append. The two rewrites that actually
    # destroyed content here (2026-07-27, restored from .bak) both shrank the
    # file; a prompt that cannot legally shrink it cannot cause that class of
    # loss at all. Consolidation is the separate, guarded path above.
    if not new or not new.lstrip().startswith("# STATE") or len(new) < len(state):
        pct = 100 - int(100 * len(new or "") / max(1, len(state)))
        log(f"REJECTED output ({len(new or '')} chars, {pct}% smaller than the "
            f"{len(state)}-char original; recovery must not shrink) — state.md left untouched")
        return

    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new if new.endswith("\n") else new + "\n")
    rotate_bak(state_path)
    os.replace(tmp, state_path)
    log(f"state.md rewritten ({len(state)} -> {len(new)} chars)")
    return True


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {type(e).__name__}: {e}")
