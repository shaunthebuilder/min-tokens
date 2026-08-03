#!/usr/bin/env python3
"""Unit tests for min-tokens hooks/recover.py. Stubs `claude` on PATH.

Covers the original Phase B behaviour plus the 7 orchestration fixes:
  1 lock  2 quiet-period (live transcript)  3 post-save cut  4 shrink floor
  5 work threshold  6 source gating  7 backup rotation
"""
import json, os, subprocess, sys, tempfile, shutil, time, fcntl, hashlib, glob, datetime

HOOK = os.environ.get("HOOK") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "recover.py")
GOOD = "# STATE — test (updated 2026-07-27)\n\n## Now\n" + ("rewritten line\n" * 200)
OLD_STATE = "# STATE — test (updated 2026-01-01)\n" + ("old line\n" * 300)

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS {name}")
    else:
        fail += 1; print(f"  FAIL {name} {extra}")


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def rec(t, content, ts=None):
    d = {"type": t, "message": {"content": content}}
    if ts:
        d["timestamp"] = iso(ts)
    return json.dumps(d)


def make_transcript(path, prose_blocks=24, bloat=True, save_after=None, save_ts=None,
                    text_mult=40):
    """save_after: insert a state.md write tool_use after this many prose blocks."""
    lines = []
    for i in range(prose_blocks):
        lines.append(rec("user", f"user prompt blk{i}mark with enough text to count"))
        lines.append(rec("assistant", [{"type": "thinking", "thinking": "SECRET-THINKING " * 50},
                                       {"type": "text", "text": f"assistant prose blk{i}mark " * text_mult},
                                       {"type": "tool_use", "name": "Read", "input": {"f": "x"}}]))
        if bloat:
            lines.append(rec("user", [{"type": "tool_result", "content": "BLOATBLOAT " * 500}]))
            lines.append(json.dumps({"type": "system", "content": "SYSNOISE " * 200}))
        if save_after is not None and i == save_after:
            lines.append(rec("assistant", [{"type": "tool_use", "name": "Edit",
                                            "input": {"file_path": "/x/.claude/state.md"}}],
                             ts=save_ts or (time.time() - 6000)))
    open(path, "w").write("\n".join(lines) + "\n")


def env_with_stub(tmp, output=GOOD, exit_code=0):
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    stub = os.path.join(bindir, "claude")
    with open(stub, "w") as f:
        f.write("#!/bin/bash\ncat > \"$0.stdin\"\nprintf '%s' \"$0.args $*\" > \"$0.argv\"\n"
                "printf '%s' \"MIN_TOKENS_CHILD=$MIN_TOKENS_CHILD\" > \"$0.env\"\n"
                f"cat <<'EOF'\n{output}\nEOF\nexit {exit_code}\n")
    os.chmod(stub, 0o755)
    e = dict(os.environ)
    e["PATH"] = bindir + os.pathsep + e["PATH"]
    e["CLAUDE_CONFIG_DIR"] = os.path.join(tmp, "cfg")
    e.pop("MIN_TOKENS_NO_RECOVER", None)
    e.pop("MIN_TOKENS_CHILD", None)
    return e, stub


def run(tmp, payload, env):
    return subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


def scenario(state_text=OLD_STATE, state_older=True, with_state=True, sid="cur123",
             prose=24, prior_age=1000, save_after=None, save_ts=None, source=None,
             text_mult=40):
    tmp = tempfile.mkdtemp()
    proj = os.path.join(tmp, "proj_transcripts"); os.makedirs(proj)
    cwd = os.path.join(tmp, "work", ".claude"); os.makedirs(cwd)
    prior = os.path.join(proj, "prior-abc.jsonl")
    make_transcript(prior, prose_blocks=prose, save_after=save_after, save_ts=save_ts,
                    text_mult=text_mult)
    old = time.time() - prior_age
    os.utime(prior, (old, old))          # past the quiet period unless told otherwise
    cur = os.path.join(proj, f"{sid}.jsonl")
    make_transcript(cur, prose_blocks=2)
    state = os.path.join(cwd, "state.md")
    if with_state:
        open(state, "w").write(state_text)
        now = time.time()
        # fixture clock: save_ts(-6000) < state.md(-5000) < prior transcript(-1000) < now-QUIET_S
        os.utime(state, (now - 5000, now - 5000) if state_older else (now + 1000, now + 1000))
    payload = {"session_id": sid, "cwd": os.path.dirname(cwd), "transcript_path": cur}
    if source:
        payload["source"] = source
    return tmp, payload, state, prior


def baks(state):
    return sorted(glob.glob(state + ".*.bak"))


print("recover.py")

# 1. happy path
tmp, payload, state, prior = scenario()
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("exit 0", r.returncode == 0, r.stderr[-300:])
check("stdout silent (never touches context)", r.stdout == "", repr(r.stdout))
check("state.md rewritten", "rewritten line" in open(state).read())
check("timestamped backup kept", len(baks(state)) == 1 and "old line" in open(baks(state)[0]).read())
check("no .tmp left", not os.path.exists(state + ".tmp"))
flag = os.path.join(tmp, "cfg", ".min-tokens-warned", "cur123-recovered")
check("re-read flag written", os.path.exists(flag))
sent = open(stub + ".stdin").read()
check("filter: prose kept", "blk3mark" in sent and "blk15mark" in sent)
check("filter: thinking dropped", "SECRET-THINKING" not in sent)
check("filter: tool_result dropped", "BLOATBLOAT" not in sent)
check("filter: system records dropped", "SYSNOISE" not in sent)
check("current state.md included", "old line" in sent)
argv = open(stub + ".argv").read()
check("model/effort/fallback/budget flags", all(x in argv for x in
      ("--model sonnet", "--effort medium", "--fallback-model haiku", "--max-budget-usd 0.50")), argv)
check("child call carries recursion guard", open(stub + ".env").read() == "MIN_TOKENS_CHILD=1",
      open(stub + ".env").read())
raw = os.path.getsize(prior)
check("filter cuts >50% of bytes", len(sent) < raw * 0.5, f"{len(sent)} vs {raw}")
shutil.rmtree(tmp)

# --- FIX 3: post-save cut -------------------------------------------------
# 2. session saved at the very end -> nothing unsaved -> skip (F3 regression)
tmp, payload, state, _ = scenario(save_after=23)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("F3 saved-at-end: no claude call", not os.path.exists(stub + ".stdin"))
check("F3 saved-at-end: state.md untouched", "old line" in open(state).read())
log = os.path.join(tmp, "cfg", ".min-tokens-warned", "recover.log")
check("F3 saved-at-end: logged as unsaved-blocks skip",
      os.path.exists(log) and "unsaved prose" in open(log).read())
shutil.rmtree(tmp)

# 3. saved early, then kept working -> only the unsaved tail is summarized
tmp, payload, state, _ = scenario(prose=40, save_after=3)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("F3 save-then-work: claude called", os.path.exists(stub + ".stdin"))
if os.path.exists(stub + ".stdin"):
    sent = open(stub + ".stdin").read()
    check("F3 save-then-work: pre-save prose excluded", "blk1mark" not in sent)
    check("F3 save-then-work: post-save prose included", "blk30mark" in sent)
shutil.rmtree(tmp)

# 4. state.md older than the recorded save (restored from backup) -> cut NOT applied
tmp, payload, state, _ = scenario(save_after=3, save_ts=time.time() - 10)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("F3 stale state.md: cut not applied, full transcript sent",
      os.path.exists(stub + ".stdin") and "blk1mark" in open(stub + ".stdin").read())
shutil.rmtree(tmp)

# --- FIX 2: quiet period --------------------------------------------------
# 5. prior transcript still being written -> defer
tmp, payload, state, prior = scenario(prior_age=5)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("F1 live transcript: no claude call", not os.path.exists(stub + ".stdin"))
check("F1 live transcript: state.md untouched", "old line" in open(state).read())
shutil.rmtree(tmp)

# --- FIX 5: work threshold ------------------------------------------------
# 6. the real observed case: a barely-started prior session (5 blocks / ~3KB)
tmp, payload, state, _ = scenario(prose=2)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("F5 trivial session: no rewrite of an accumulated state file",
      not os.path.exists(stub + ".stdin") and "old line" in open(state).read())
shutil.rmtree(tmp)

# 6b. few blocks but a LOT of prose (the observed 2 blocks / 50395 chars). Block
# count used to veto volume and this work was discarded three times; either
# signal now qualifies.
tmp, payload, state, _ = scenario(prose=1, text_mult=2000)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("F5 volume alone qualifies: 2 blocks / >15K chars is recovered",
      os.path.exists(stub + ".stdin") and "rewritten line" in open(state).read())
shutil.rmtree(tmp)

# --- FIX 4 / step 1: recovery is APPEND-ONLY ------------------------------
# 7. output that keeps only ~half -> rejected
half = "# STATE — test\n" + ("kept line\n" * 130)   # ~1.4K vs ~2.7K original
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp, output=half)
r = run(tmp, payload, env)
check("F4 50% shrink rejected", "old line" in open(state).read())
log = os.path.join(tmp, "cfg", ".min-tokens-warned", "recover.log")
check("F4 rejection logged loudly", "REJECTED" in open(log).read())
check("F4 no backup churn on rejection", baks(state) == [])
shutil.rmtree(tmp)

# 8. a merely MODEST shrink is now rejected too: append-only means len(new) >= len(A)
mild = "# STATE — test\n" + ("old line\n" * 290)    # ~97% of the original
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp, output=mild)
r = run(tmp, payload, env)
check("append-only: 3% shrink rejected", len(open(state).read()) >= len(OLD_STATE))
check("append-only: rejection names the rule",
      "must not shrink" in open(os.path.join(tmp, "cfg", ".min-tokens-warned", "recover.log")).read())
shutil.rmtree(tmp)

# 8b. equal-length output is accepted (the floor is >=, not >)
# +1 char to offset the trailing newline the stub's heredoc loses to .strip(),
# so this lands EXACTLY on the len(new) == len(state) boundary.
same = OLD_STATE.replace("2026-01-01", "2026-07-28") + "x"
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp, output=same)
r = run(tmp, payload, env)
check("append-only: same-length output accepted", "2026-07-28" in open(state).read())
shutil.rmtree(tmp)

# --- STEP 2: consolidate mode ---------------------------------------------
# The structural point: it must fire with ZERO unsaved prose (save_after=23), or
# a user with perfect save hygiene never consolidates at all.
EVID = "evidence line\n" * 60
RESIDENT = "resident line\n" * 40
CONS_HEAD = ("## Goal\ng\n## Now\nn\n## Decisions\n- rejected X because Y\n## Map\n" + RESIDENT
             + "## Constraints & gotchas\nc\n## Next\n1. thing\n")
CONS_STATE = "# STATE — test (updated 2026-01-01)\n" + CONS_HEAD + "## Prior (v1)\n" + EVID
CONS_NEW = ("# STATE — test (updated 2026-07-28)\n" + CONS_HEAD
            + "- v1 shipped and is superseded — full detail in `.claude/notes/x.md`\n")
CONS_NOTE = "# archive\n" + EVID
TWO = f"=== STATE ===\n{CONS_NEW}\n=== NOTE ===\n{CONS_NOTE}"


def cons_scenario(**kw):
    """Over-cap state.md, prior thread saved at the end -> nothing to recover."""
    tmp, payload, state, prior = scenario(state_text=CONS_STATE, save_after=23, **kw)
    return tmp, payload, state


def cons_env(tmp, output=TWO, cap="500"):
    env, stub = env_with_stub(tmp, output=output)
    env["MIN_TOKENS_STATE_CAP"] = cap
    return env, stub


# 9. fires on size alone, with 0 unsaved blocks
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp)
r = run(tmp, payload, env)
note = os.path.join(payload["cwd"], ".claude", "notes")
notes = glob.glob(os.path.join(note, "*.md"))
check("consolidate: fires with 0 unsaved blocks when over cap", os.path.exists(stub + ".stdin"))
check("consolidate: state.md shrunk", len(open(state).read()) < len(CONS_STATE))
check("consolidate: note written", len(notes) == 1, notes)
check("consolidate: evidence moved VERBATIM", notes and EVID in open(notes[0]).read())
check("consolidate: combined bytes preserved",
      notes and len(open(state).read()) + len(open(notes[0]).read()) >= len(CONS_STATE))
check("consolidate: rejected decision still resident", "rejected X because Y" in open(state).read())
check("consolidate: backup taken", len(baks(state)) == 1)
check("consolidate: re-read flag written", os.path.exists(
      os.path.join(tmp, "cfg", ".min-tokens-warned", "cur123-recovered")))
shutil.rmtree(tmp)

# 10. under cap and no marker -> does not fire
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp, cap="900000")
r = run(tmp, payload, env)
check("consolidate: silent under cap", not os.path.exists(stub + ".stdin"))
shutil.rmtree(tmp)

# 11. the marker dropped by an in-thread save fires it under cap, and is cleared
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp, cap="900000")
warn = os.path.join(tmp, "cfg", ".min-tokens-warned"); os.makedirs(warn, exist_ok=True)
mk = os.path.join(warn, hashlib.sha1(os.path.abspath(payload["cwd"]).encode()).hexdigest()[:12] + "-consolidate")
open(mk, "w").close()
r = run(tmp, payload, env)
check("consolidate: marker fires it under cap", os.path.exists(stub + ".stdin"))
check("consolidate: marker cleared after success", not os.path.exists(mk))
shutil.rmtree(tmp)

# 12. rejects output that drops a protected heading
bad = TWO.replace("## Next\n1. thing\n", "")
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp, output=bad)
r = run(tmp, payload, env)
check("consolidate: protected heading dropped -> rejected", open(state).read() == CONS_STATE)
check("consolidate: rejection logged",
      "protected heading" in open(os.path.join(tmp, "cfg", ".min-tokens-warned", "recover.log")).read())
shutil.rmtree(tmp)

# 13. rejects output whose COMBINED bytes shrink (the move-not-shorten check)
lossy = f"=== STATE ===\n{CONS_NEW}\n=== NOTE ===\n# archive\n" + ("evidence line\n" * 10)
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp, output=lossy)
r = run(tmp, payload, env)
check("consolidate: combined shrink -> rejected", open(state).read() == CONS_STATE)
check("consolidate: shrink rejection names move-not-shorten",
      "move-not-shorten" in open(os.path.join(tmp, "cfg", ".min-tokens-warned", "recover.log")).read())
shutil.rmtree(tmp)

# 14. an existing notes/ file is NEVER rewritten (append-only archive)
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp)
nd = os.path.join(payload["cwd"], ".claude", "notes"); os.makedirs(nd)
existing = os.path.join(nd, time.strftime("%Y-%m") + "-archive.md")
open(existing, "w").write("PRECIOUS")
r = run(tmp, payload, env)
check("consolidate: existing note untouched", open(existing).read() == "PRECIOUS")
check("consolidate: new note written alongside", len(glob.glob(os.path.join(nd, "*.md"))) == 2)
shutil.rmtree(tmp)

# 15. malformed reply (one document only) -> rejected
tmp, payload, state = cons_scenario()
env, stub = cons_env(tmp, output=CONS_NEW)
r = run(tmp, payload, env)
check("consolidate: single-document reply rejected", open(state).read() == CONS_STATE)
check("consolidate: no note written",
      not os.path.isdir(os.path.join(payload["cwd"], ".claude", "notes")))
shutil.rmtree(tmp)

# 15b. REGRESSION (live, 2026-07-28): the state document QUOTES both markers
# inline, because state.md documents consolidate mode. A substring split cut the
# reply inside the reproduced state text — 5 chars of state, the whole file as the
# note — and every consolidation on that project was rejected as malformed for a
# day, each attempt a real ~100s `claude -p` call. Markers count only alone on a line.
SELF = "- consolidate emits `=== STATE ===` / `=== NOTE ===`, guarded by combined-bytes\n"
SELF_STATE = "# STATE — test (updated 2026-01-01)\n" + CONS_HEAD + SELF + "## Prior (v1)\n" + EVID
SELF_NEW = ("# STATE — test (updated 2026-07-28)\n" + CONS_HEAD + SELF
            + "- v1 shipped and is superseded — full detail in `.claude/notes/x.md`\n")
SELF_TWO = f"=== STATE ===\n{SELF_NEW}\n=== NOTE ===\n{CONS_NOTE}"
tmp, payload, state, _ = scenario(state_text=SELF_STATE, save_after=23)
env, stub = cons_env(tmp, output=SELF_TWO)
r = run(tmp, payload, env)
got = open(state).read()
check("consolidate: inline-quoted markers do not split the document",
      got.startswith("# STATE") and "## Goal" in got, repr(got[:120]))
check("consolidate: the self-referential line survives verbatim", SELF in got)
selfnotes = glob.glob(os.path.join(payload["cwd"], ".claude", "notes", "*.md"))
check("consolidate: note written despite inline markers",
      len(selfnotes) == 1 and EVID in open(selfnotes[0]).read(), selfnotes)
shutil.rmtree(tmp)

# --- FIX 6: source gating -------------------------------------------------
for src, want in (("startup", True), ("clear", True), ("compact", False), ("resume", False)):
    tmp, payload, state, _ = scenario(source=src)
    env, stub = env_with_stub(tmp)
    r = run(tmp, payload, env)
    got = os.path.exists(stub + ".stdin")
    check(f"F6 source={src}: {'runs' if want else 'skipped'}", got == want)
    shutil.rmtree(tmp)

# --- FIX 1: lock ----------------------------------------------------------
# 9. a held lock makes a second recovery a no-op
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
warn = os.path.join(tmp, "cfg", ".min-tokens-warned"); os.makedirs(warn, exist_ok=True)
key = hashlib.sha1(os.path.abspath(payload["cwd"]).encode()).hexdigest()[:12]
held = open(os.path.join(warn, f"recover-{key}.lock"), "w")
fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
r = run(tmp, payload, env)
check("F2 lock held: no claude call", not os.path.exists(stub + ".stdin"))
check("F2 lock held: state.md untouched", "old line" in open(state).read())
check("F2 lock held: logged", "holds the lock" in open(os.path.join(warn, "recover.log")).read())
fcntl.flock(held, fcntl.LOCK_UN); held.close()
r = run(tmp, payload, env)
check("F2 lock released: runs normally", os.path.exists(stub + ".stdin"))
shutil.rmtree(tmp)

# 10. two real concurrent recoveries -> exactly one rewrite
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
slow = os.path.join(tmp, "bin", "claude")
with open(slow, "w") as f:
    f.write("#!/bin/bash\ncat > /dev/null\nsleep 2\n"
            f"printf '%s' x >> \"$0.calls\"\ncat <<'EOF'\n{GOOD}\nEOF\n")
os.chmod(slow, 0o755)
ps = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, env=env) for _ in range(2)]
for p in ps:
    p.communicate(json.dumps(payload), timeout=60)
calls = open(slow + ".calls").read() if os.path.exists(slow + ".calls") else ""
check("F2 concurrent: exactly one summarizer call", len(calls) == 1, repr(calls))
check("F2 concurrent: exactly one backup", len(baks(state)) == 1, baks(state))
shutil.rmtree(tmp)

# --- FIX 7: backup rotation ----------------------------------------------
# 11. repeated recoveries keep the newest 3 backups, not 1
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
for i in range(5):
    open(state, "w").write(OLD_STATE)
    now = time.time(); os.utime(state, (now - 5000, now - 5000))
    for b in baks(state):
        os.utime(b, (now - 5000 + i, now - 5000 + i))
    run(tmp, payload, env)
    time.sleep(1.05)   # backup names are second-resolution
check("F7 rotation keeps 3 backups", len(baks(state)) == 3, baks(state))
check("F7 single-slot .bak no longer used", not os.path.exists(state + ".bak"))
shutil.rmtree(tmp)

# --- original guards, unchanged ------------------------------------------
# 12. state.md newer than prior transcript -> sealed, skip
tmp, payload, state, _ = scenario(state_older=False)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("sealed state: no claude call", not os.path.exists(stub + ".stdin"))
check("sealed state: untouched", "old line" in open(state).read())
shutil.rmtree(tmp)

# 13. no state.md -> opt-out
tmp, payload, state, _ = scenario(with_state=False)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("no state.md: no call", not os.path.exists(stub + ".stdin"))
check("no state.md: not created", not os.path.exists(state))
shutil.rmtree(tmp)

# 14. only the current session's transcript exists -> nothing prior
tmp, payload, state, prior = scenario()
os.remove(prior)
env, stub = env_with_stub(tmp)
r = run(tmp, payload, env)
check("current session's own jsonl excluded", not os.path.exists(stub + ".stdin"))
shutil.rmtree(tmp)

# 15. bad model output -> refuse to overwrite
for label, out in (("non-state output", "Sure! Here is the summary:\nstuff"),
                   ("truncated output", "# STATE — x\nshort")):
    tmp, payload, state, _ = scenario()
    env, stub = env_with_stub(tmp, output=out)
    r = run(tmp, payload, env)
    check(f"{label}: state.md preserved", "old line" in open(state).read())
    check(f"{label}: no flag", not os.path.exists(
        os.path.join(tmp, "cfg", ".min-tokens-warned", "cur123-recovered")))
    shutil.rmtree(tmp)

# 16. claude fails
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp, output="boom", exit_code=1)
r = run(tmp, payload, env)
check("claude nonzero: exit 0", r.returncode == 0)
check("claude nonzero: state preserved", "old line" in open(state).read())
log = os.path.join(tmp, "cfg", ".min-tokens-warned", "recover.log")
check("failure logged", os.path.exists(log) and "exited 1" in open(log).read())
shutil.rmtree(tmp)

# 17. off flag
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
os.makedirs(os.path.join(tmp, "cfg"), exist_ok=True)
open(os.path.join(tmp, "cfg", ".min-tokens-off"), "w").close()
r = run(tmp, payload, env)
check("off flag honored", not os.path.exists(stub + ".stdin"))
shutil.rmtree(tmp)

# 18. env kill switch
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
env["MIN_TOKENS_NO_RECOVER"] = "1"
r = run(tmp, payload, env)
check("MIN_TOKENS_NO_RECOVER honored", not os.path.exists(stub + ".stdin"))
shutil.rmtree(tmp)

# 19. recursion guard: recover.py must be inert inside its own child session
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
env["MIN_TOKENS_CHILD"] = "1"
r = run(tmp, payload, env)
check("MIN_TOKENS_CHILD: no recursive call", not os.path.exists(stub + ".stdin"))
shutil.rmtree(tmp)

# 20. session-start.sh must inject nothing and launch nothing inside a child session.
# The script lives beside whichever recover.py we're testing, so HOOK=<cache> tests
# the cache copy. CLAUDE_CONFIG_DIR must point at a scratch dir: inheriting the real
# one means a live `/min-tokens off` flag silently fails the injection assertion.
sh = os.path.join(os.path.dirname(os.path.abspath(HOOK)), "session-start.sh")
cfg = tempfile.mkdtemp()
e = dict(os.environ, MIN_TOKENS_CHILD="1", CLAUDE_CONFIG_DIR=cfg)
r = subprocess.run(["bash", sh], input="{}", capture_output=True, text=True, env=e, timeout=30)
check("session-start.sh silent under MIN_TOKENS_CHILD", r.stdout == "" and r.returncode == 0, repr(r.stdout[:80]))
r = subprocess.run(["bash", sh], input="{}", capture_output=True, text=True,
                   env=dict(os.environ, MIN_TOKENS_NO_RECOVER="1", CLAUDE_CONFIG_DIR=cfg), timeout=30)
check("session-start.sh still injects rules normally", "MIN-TOKENS ACTIVE" in r.stdout)
shutil.rmtree(cfg, ignore_errors=True)

# 21. malformed stdin
tmp, payload, state, _ = scenario()
env, stub = env_with_stub(tmp)
r = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True,
                   text=True, env=env, timeout=30)
check("malformed stdin: exit 0, silent", r.returncode == 0 and r.stdout == "")
shutil.rmtree(tmp)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
