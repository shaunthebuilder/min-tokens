#!/usr/bin/env python3
"""Tests for the context gate in context-watch.py. Run: python3 t_gate.py"""
import json, os, subprocess, sys, tempfile, shutil

HOOK = os.environ.get("HOOK") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "context-watch.py")
fails = []


def run(prompt, ctx, sid="s1", cfg=None, env_extra=None):
    """Feed the hook one prompt at a given context size. Returns (stdout, cfgdir)."""
    cfg = cfg or tempfile.mkdtemp()
    tp = os.path.join(cfg, f"{sid}.jsonl")
    with open(tp, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"usage": {
            "input_tokens": ctx, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}}}) + "\n")
    env = dict(os.environ, CLAUDE_CONFIG_DIR=cfg)
    env.pop("MIN_TOKENS_SOFT", None)
    env.update(env_extra or {})
    payload = json.dumps({"prompt": prompt, "transcript_path": tp, "session_id": sid})
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, f"nonzero exit: {r.stderr}"
    return r.stdout, cfg


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


REMINDER = open(os.path.join(os.path.dirname(HOOK), "style-reminder.md")).read().strip()


def quiet(out):
    """Silent apart from the per-turn response-contract reminder, which is every turn."""
    return out.strip() == REMINDER


def blocked(out):
    try:
        d = json.loads(out)
        return d.get("decision") == "block" and d.get("reason") or None
    except Exception:
        return None


# 1. Under the ceiling: silent passthrough.
out, _ = run("hello", 50000)
check("under 95K is silent", quiet(out), repr(out))

# 2. At the ceiling: blocks, shows all three choices, user-visible.
out, cfg = run("do the thing", 96000)
r = blocked(out)
check("95K blocks", r is not None, repr(out))
check("95K offers go/save/new", r and all(w in r for w in ("go", "save", "new")), r)
check("95K states context size", r and "~96K" in r, r)

# 3. Prompt is stashed, not lost.
stash = json.load(open(os.path.join(cfg, ".min-tokens-warned", "s1-gate.json")))
check("prompt stashed verbatim", stash["prompt"] == "do the thing", stash)

# 4. `go` re-injects the held prompt, is not a block, and costs no save.
out, _ = run("go", 96000, cfg=cfg)
check("go is not blocked", blocked(out) is None, out)
check("go re-injects held prompt", "do the thing" in out, out)
check("go under 125K does not force save", "backstop" not in out, out)
check("stash cleared after go", not os.path.exists(
    os.path.join(cfg, ".min-tokens-warned", "s1-gate.json")))

# 5. Next message gates again — no once-per-session latch.
out, cfg = run("second message", 96000, cfg=cfg)
check("gate fires again on next message", blocked(out) is not None, out)

# 6. `save` instructs patching, not re-emission, and still answers the held prompt.
out, _ = run("save", 96000, cfg=cfg)
check("save instructs Edit patch", "PATCH" in out or "Edit" in out, out)
check("save forbids re-emit", "re-emit" in out or "re-read" in out, out)
check("save carries held prompt", "second message" in out, out)

# 7. `new` records the held prompt into the single `## Held` slot, unanswered.
out, cfg = run("third message", 96000)
out, _ = run("new", 96000, cfg=cfg)
check("new writes held msg to ## Held", "## Held" in out and "third message" in out, out)
check("new does not append to ## Next", "## Next" not in out, out)
check("new says Held is one slot, overwritten",
      "REPLACING" in out and "not a " in out, out)
# Two consecutive handoffs must leave ONE held message, not a growing list.
out2, cfg2 = run("fourth message", 96000)
out2, _ = run("new", 96000, cfg=cfg2)
check("second new overwrites rather than appends",
      "fourth message" in out2 and "third message" not in out2, out2)
check("new says do not answer", "Do NOT answer" in out, out)
check("new tells user to start a new thread", "new thread" in out, out)

# 8. Past 125K, `go` also fires the save backstop — once.
out, cfg = run("work", 130000)
check("125K gate warns go forces save", "force" in (blocked(out) or ""), blocked(out))
out, _ = run("go", 130000, cfg=cfg)
check("125K go fires backstop", "backstop" in out, out)
check("125K go still answers held prompt", "work" in out, out)
out, _ = run("more work", 131000, cfg=cfg)   # gates again
out2, _ = run("go", 131000, cfg=cfg)
check("backstop fires only once", "backstop" not in out2, out2)

# 9. An explicit `save` suppresses the later backstop (no double save).
out, cfg = run("w", 130000)
out, _ = run("save", 130000, cfg=cfg)
out, _ = run("w2", 131000, cfg=cfg)
out2, _ = run("go", 131000, cfg=cfg)
check("save suppresses backstop", "backstop" not in out2, out2)

# 10. Past the hard ceiling: still gated, wording escalates, `go` still available.
out, _ = run("x", 155000)
r = blocked(out)
check("hard ceiling still gates", r is not None)
check("hard ceiling escalates wording", r and "OVER" in r, r)
check("hard ceiling keeps go", r and "go" in r, r)

# 11. An unrecognized reply replaces the held message and re-gates (nothing lost).
out, cfg = run("original", 96000)
out, _ = run("actually do this instead", 96000, cfg=cfg)
check("non-answer re-gates", blocked(out) is not None, out)
stash = json.load(open(os.path.join(cfg, ".min-tokens-warned", "s1-gate.json")))
check("non-answer replaces stash", stash["prompt"] == "actually do this instead", stash)

# 12. A bare answer word with nothing held is NOT swallowed — it gates like any prompt.
out, cfg = run("save", 96000)
check("bare 'save' with empty stash gates", blocked(out) is not None, out)
stash = json.load(open(os.path.join(cfg, ".min-tokens-warned", "s1-gate.json")))
check("bare 'save' is stashed as a prompt", stash["prompt"] == "save", stash)

# 13. /min-tokens off disables the gate entirely.
out, cfg = run("q", 96000)
open(os.path.join(cfg, ".min-tokens-off"), "w").close()
out, _ = run("q2", 96000, cfg=cfg)
check("off-flag disables gate", out == "", repr(out))

# 14. /min-tokens commands are not eaten by the gate.
out, cfg = run("/min-tokens on", 96000)
r = blocked(out)
check("/min-tokens on still answered", r is not None and "min-tokens on" in r, r)

# 15. Dropping back under the ceiling clears a stale stash.
out, cfg = run("held", 96000)
out, _ = run("later", 40000, cfg=cfg)
check("under-ceiling clears stash", not os.path.exists(
    os.path.join(cfg, ".min-tokens-warned", "s1-gate.json")))

# 16. Threshold is env-tunable and the gate honours it.
out, _ = run("x", 30000, env_extra={"MIN_TOKENS_SOFT": "25000"})
check("MIN_TOKENS_SOFT honoured", blocked(out) is not None, out)

# 17. Garbage / missing transcript never blocks the session.
r = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True)
check("garbage input exits 0 silently", r.returncode == 0 and r.stdout == "", r.stdout)
r = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": "hi"}),
                   capture_output=True, text=True,
                   env=dict(os.environ, CLAUDE_CONFIG_DIR=tempfile.mkdtemp()))
check("missing transcript exits 0 silently", r.returncode == 0 and quiet(r.stdout), r.stdout)

# 18. Multi-line prompt beginning with "go" is a prompt, not an answer.
out, cfg = run("held one", 96000)
out, _ = run("go\nand also fix the parser", 96000, cfg=cfg)
check("multiline 'go ...' is not an answer", blocked(out) is not None, out)

# 19. Case and whitespace tolerance on answers.
out, cfg = run("held two", 96000)
out, _ = run("  GO  ", 96000, cfg=cfg)
check("answers are case/space tolerant", blocked(out) is None and "held two" in out, out)

# 20. /save works below the ceiling, with nothing held.
out, _ = run("/save", 40000)
check("/save under ceiling instructs patch", "PATCH" in out and "wc -c" in out, out)
check("/save under ceiling is not a block", blocked(out) is None, out)
check("/save suggests a new thread", "new thread" in out, out)

# 21. /save is transparent to the gate: it is never held, and it releases the
#     held prompt instead of stranding it.
out, cfg = run("fix the parser", 96000)
check("gate held the real prompt", blocked(out) is not None, out)
out, _ = run("/save", 96000, cfg=cfg)
check("/save is not gated", blocked(out) is None, out)
check("/save carries the held prompt", "fix the parser" in out, out)
check("/save clears the stash", not os.path.exists(
    os.path.join(cfg, ".min-tokens-warned", "s1-gate.json")))

# 22. /save past 125K counts as the save, so the backstop does not double-fire.
out, cfg = run("w", 130000)
out, _ = run("/save", 130000, cfg=cfg)
out, _ = run("w2", 131000, cfg=cfg)
out2, _ = run("go", 131000, cfg=cfg)
check("/save suppresses backstop", "backstop" not in out2, out2)

# 23. /min-tokens save takes the same path (no skill round-trip needed).
out, _ = run("/min-tokens save", 40000)
check("/min-tokens save handled by hook", "PATCH" in out, out)

# 24. An expanded commands/save.md is recognized by its marker.
out, cfg = run("held msg", 96000)
out, _ = run("<!-- min-tokens:save-command -->\nRun the save procedure.", 96000, cfg=cfg)
check("expanded save command recognized", blocked(out) is None and "held msg" in out, out)

# 25. /save still works with the off-flag set (explicit user request wins).
out, cfg = run("q", 40000)
open(os.path.join(cfg, ".min-tokens-off"), "w").close()
out, _ = run("/save", 40000, cfg=cfg)
check("/save works while off", "PATCH" in out, out)

# 26. /save with no transcript still saves rather than crashing.
r = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": "/save"}),
                   capture_output=True, text=True)
check("/save without transcript still instructs", r.returncode == 0 and "PATCH" in r.stdout, r.stdout)

# 26b. Every spelling of the command reaches handle_save, gated or not.
for form in ("/save", "/SAVE", "  /save  ", "/min-tokens:save", "/min-tokens save",
             "/min-tokens:min-tokens save"):
    out, cfg = run("held work", 96000)
    out, _ = run(form, 96000, cfg=cfg)
    check(f"{form.strip()!r} bypasses the gate",
          blocked(out) is None and "PATCH" in out and "held work" in out, out)

# 27. "save the file" is a normal prompt, not the command.
out, _ = run("save the config file", 40000)
check("'save the ...' is not /save", quiet(out), repr(out))

# 28. The response contract reaches the model at session start.
# Asserts on the HOOK'S OUTPUT, not on the file existing: with style.md present
# but the session-start.sh edit lost, a file check passes, nothing goes to
# stderr, and the contract is silently absent. That is the likelier failure.
HOOKS = os.path.dirname(HOOK)
out = subprocess.run(["bash", os.path.join(HOOKS, "session-start.sh")], input="{}",
                     capture_output=True, text=True,
                     env=dict(os.environ, CLAUDE_CONFIG_DIR=tempfile.mkdtemp(),
                              MIN_TOKENS_NO_RECOVER="1")).stdout
check("style.md reaches the model", "RESPONSE CONTRACT" in out, out[-200:])

# 29. The per-turn reminder reaches the model, and never rides with a block.
out, _ = run("normal turn", 40000)
check("per-turn reminder reaching the model", "RESPONSE CONTRACT" in out, out)
out, _ = run("big turn", 96000)
check("reminder never printed alongside a block",
      blocked(out) is not None and "RESPONSE CONTRACT" not in out, out)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
