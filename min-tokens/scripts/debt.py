#!/usr/bin/env python3
"""min-tokens debt ledger — harvest every `min:` marker into one list.

Rule 10 tells the model to mark deliberate shortcuts with a `min:` comment
naming the ceiling and the upgrade path. Nothing ever read them back, so the
convention was decorative: markers scattered across files that no one greps.
This makes it load-bearing — one command, one ledger.

Pure stdlib, offline, zero model tokens (context-watch.py blocks the prompt and
prints this output directly). Also harvests legacy `ponytail:` markers, since
ponytail's ladder was absorbed into Rule 10 and its debt skill was not.
"""
import os, re, sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", ".next", "target", ".mypy_cache", ".pytest_cache", "vendor"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".ico",
            ".woff", ".woff2", ".ttf", ".mp4", ".so", ".dylib", ".pyc", ".lock"}
MAX_BYTES = 512_000  # a marker never lives in a file this big; skip data blobs

# A marker is `min:` / `ponytail:` immediately after a comment opener, so a
# stray "min:" inside prose or a dict key never counts.
MARKER = re.compile(r"(?:#|//|/\*|--|;|%|<!--|\*)\s*(min|ponytail):\s*(\S.*?)\s*(?:\*/|-->)?$")


def scan(root):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="strict") as f:
                    for n, line in enumerate(f, 1):
                        m = MARKER.search(line.rstrip("\n"))
                        if m:
                            hits.append((os.path.relpath(path, root), n, m.group(1), m.group(2)))
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable: not where markers live
    return hits


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    hits = scan(root)
    if not hits:
        print("debt ledger: no `min:` markers in " + os.path.basename(os.path.abspath(root)) +
              " — nothing deferred, or nothing marked.")
        return
    out = [f"debt ledger — {len(hits)} deliberate shortcut(s) in "
           f"{os.path.basename(os.path.abspath(root))}:"]
    last = None
    for path, n, kind, note in sorted(hits):
        if path != last:
            out.append(path)
            last = path
        # The note IS the ceiling + upgrade path; Rule 10 mandates both. Print
        # it verbatim — paraphrasing a shortcut's ceiling is how it gets missed.
        out.append(f"  :{n}  {note}" + ("   [legacy ponytail marker]" if kind == "ponytail" else ""))
    out.append("Each line names its ceiling and upgrade path. Fix one, delete its marker.")
    print("\n".join(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"debt ledger unavailable: {e}")
