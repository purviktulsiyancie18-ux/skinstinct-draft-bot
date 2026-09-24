"""Triage report for the existing backlog (e.g. the ~60 fragments already in her channel).

    .venv/bin/python import_backlog.py result.json      # Telegram Desktop export (JSON)
    .venv/bin/python import_backlog.py notes.txt        # notes separated by a line of ---

Prints a verdict per fragment, strongest first. Forward the ones worth a post
to the bot to get drafts. Nothing is sent or stored.
"""
import json
import sys
import time
from pathlib import Path

import pipeline


def load_notes(path):
    path = Path(path)
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        notes = []
        for m in data.get("messages", []):
            if m.get("type") != "message":
                continue
            text = m.get("text")
            if isinstance(text, list):  # rich text comes as a list of strings/entities
                text = "".join(t if isinstance(t, str) else t.get("text", "") for t in text)
            if text and text.strip():
                notes.append((f"backlog:{data.get('id', path.stem)}:{m['id']}", text.strip(), m.get("date")))
        return notes
    raw = path.read_text()
    blocks = raw.split("\n---\n") if "\n---\n" in raw else raw.split("\n\n")
    return [(f"backlog:{path.name}:{i}", b.strip(), None) for i, b in enumerate(blocks) if b.strip()]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    results = []
    for _ref, text, date in load_notes(sys.argv[1]):
        try:
            t = pipeline.triage(text, received=(date or "unknown date")[:10])
        except Exception as exc:
            print(f"  ! triage failed: {exc}")
            continue
        results.append((t, text))
        print(f"[{t.verdict:>7} {t.score:>2}/10] {text[:70]!r}")
        time.sleep(1)  # stay well inside free-tier rate limits

    print("\nWorth a post, strongest first:\n")
    for t, text in sorted(results, key=lambda r: -r[0].score):
        if t.verdict == "develop":
            print(f"{t.score}/10  {t.angle}\n       note: {text[:120]!r}\n")
    counts = {v: sum(1 for t, _ in results if t.verdict == v) for v in ("develop", "hold", "discard")}
    print(f"{counts['develop']} worth a post, {counts['hold']} need more detail, {counts['discard']} not posts.")


if __name__ == "__main__":
    main()
