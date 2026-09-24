"""Triage the existing backlog (e.g. the ~60 fragments already in her channel).

    .venv/bin/python import_backlog.py result.json      # Telegram Desktop export (JSON)
    .venv/bin/python import_backlog.py notes.txt        # notes separated by a line of ---
    add --dry-run to see verdicts without saving

Nothing is sent to Telegram per note; strong notes join the normal queue and get
drafted at the usual pace, with a fresh reference at drafting time.
"""
import json
import sys
import time
from pathlib import Path

import pipeline
import store


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
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    conn = store.connect()
    tally = {"develop": 0, "hold": 0, "discard": 0}

    for ref, text, date in load_notes(args[0]):
        note_id = None
        if not dry:
            note_id = store.add_note(conn, "backlog", ref, text, received_at=date)
            if note_id is None:
                continue  # already imported
        try:
            t = pipeline.triage(text, received=(date or "unknown date")[:10])
        except Exception as exc:
            print(f"  ! triage failed, left as 'new': {exc}")
            continue
        tally[t.verdict] += 1
        print(f"[{t.verdict:>7} {t.score:>2}/10] {text[:70]!r}\n            {t.reason}")
        if note_id:
            status = {"develop": "queued", "hold": "held", "discard": "discarded"}[t.verdict]
            store.set_triage(conn, note_id, status, t.score, t.model_dump())
        time.sleep(1)  # stay well inside free-tier rate limits

    print(f"\nDone: {tally['develop']} queued, {tally['hold']} held, {tally['discard']} discarded.")


if __name__ == "__main__":
    main()
