---
description: Save/update .claude/state.md so you can start a new thread (same as /min-tokens save)
---

<!-- min-tokens:save-command -->

Run the `/min-tokens save` procedure (`skills/min-tokens/SKILL.md`) — do NOT read the
skill file if the hook already injected the procedure into this turn.

1. `wc -c .claude/state.md`.
2. PATCH it with `Edit` calls touching only the sections that changed. state.md is
   already in context from session start: never re-read it, never re-emit it whole.
   A full `Write` is only for a file that does not exist yet.
3. Over 20000 chars: still just patch, then drop the consolidate marker so the
   off-context consolidator does the rewrite for free —
   ```bash
   python3 -c "import hashlib,os;d=os.path.expanduser('${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.min-tokens-warned');os.makedirs(d,exist_ok=True);open(os.path.join(d,hashlib.sha1(os.path.abspath('.').encode()).hexdigest()[:12]+'-consolidate'),'w').close()"
   ```
4. Confirm in ONE line. No summary, no diff, no file echo.
