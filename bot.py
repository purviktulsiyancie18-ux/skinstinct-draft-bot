"""Skinstinct draft bot - Telegram in, reviewed-by-Meera drafts out.

    .venv/bin/python bot.py

Flow: note arrives -> triage (develop / hold / discard) -> developable notes are
queued -> on delivery days (default Mon/Wed/Fri) the strongest queued note gets a
fresh current reference and a draft, sent to Meera's chat. It stops there.
"""
import json
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import pipeline
import store
import telegram_api as tg

HELP = """I turn your notes into LinkedIn drafts for you to review. I never post anything.

Send or forward me any note - typed or a voice note - or post it in your notes channel. I'll tell you whether it's worth a post. Good ones get queued and drafted on {days} at {time}.

/draft - draft the strongest queued note now
/draft 12 - draft note 12 now
/queue - what's waiting
/redo 12 shorter, lead with the Chennai data - redraft with your notes
/keep 12 - queue a note I discarded (you overrule me)
/drop 12 - remove a note from the queue
/status - counts

Reply to a draft with changes and I'll redraft it. Reply to a "holding" message with the missing detail and I'll re-check it."""


# ---------------------------------------------------------------- ingest

def note_text(msg):
    """Text of a message; voice notes and audio files are transcribed by Gemini.
    Returns (text, was_voice)."""
    text = (msg.get("text") or msg.get("caption") or "").strip()
    media = msg.get("voice") or msg.get("audio") or msg.get("video_note")
    if not media:
        return text, False
    if config.OWNER_CHAT_ID:
        tg.typing(config.OWNER_CHAT_ID)
    mime = media.get("mime_type") or ("video/mp4" if "video_note" in msg else "audio/ogg")
    spoken = pipeline.transcribe(tg.download(media["file_id"]), mime)
    return "\n\n".join(t for t in (text, spoken) if t), True


def ingest(conn, text, source, source_ref, heard=False):
    note_id = store.add_note(conn, source, source_ref, text)
    if note_id is None:
        return  # already seen
    run_triage(conn, note_id, heard)


def run_triage(conn, note_id, heard=False):
    note = store.get_note(conn, note_id)
    tg.typing(config.OWNER_CHAT_ID)
    t = pipeline.triage(note["text"], received=note["received_at"][:10])
    status = {"develop": "queued", "hold": "held", "discard": "discarded"}[t.verdict]
    store.set_triage(conn, note_id, status, t.score, t.model_dump())

    if t.verdict == "develop":
        msg = (f"Queued #{note_id} ({t.score}/10, {t.category}): {t.angle}\n\n"
               f"Next scheduled draft: {next_slot()}. Or /draft {note_id} now.")
    elif t.verdict == "hold":
        msg = (f"Holding #{note_id}: {t.reason}\n\nTo make it a post: {t.missing}\n"
               f"Reply to this message with that and I'll re-check.")
    else:
        msg = f"Not a post #{note_id}: {t.reason}\n\n(/keep {note_id} if you disagree.)"
    if heard:
        msg = f"Heard: \"{note['text']}\"\n\n{msg}"
    store.set_ack(conn, note_id, tg.send(config.OWNER_CHAT_ID, msg))


# ---------------------------------------------------------------- drafting

def deliver(conn, note_id, feedback=None):
    note = store.get_note(conn, note_id)
    if note is None:
        return tg.send(config.OWNER_CHAT_ID, f"No note #{note_id}.")
    triage = json.loads(note["triage_json"] or "{}")
    if not triage.get("angle"):
        # e.g. /keep on a discarded note with no angle - ask the model for one
        triage = pipeline.triage(note["text"]).model_dump()
    tg.typing(config.OWNER_CHAT_ID)

    previous = store.latest_draft(conn, note_id)
    if feedback and previous:
        reference = json.loads(previous["reference_json"] or "{}")  # keep the same reference on a redo
    else:
        reference = pipeline.find_reference(triage["angle"], triage.get("search_queries") or triage.get("search_query"))
    tg.typing(config.OWNER_CHAT_ID)

    body, issues = pipeline.draft_post(
        note["text"], triage, reference,
        feedback=feedback, previous=previous["body"] if previous else None,
    )
    store.add_draft(conn, note_id, reference, body, issues, feedback)

    post_msg = tg.send(config.OWNER_CHAT_ID, body)
    store.set_ack(conn, note_id, post_msg)  # replying to the post = redo with feedback
    tg.send(config.OWNER_CHAT_ID, review_card(note, triage, reference, body, issues))


def review_card(note, triage, reference, body, issues):
    excerpt = note["text"].replace("\n", " ")
    excerpt = excerpt[:160] + ("..." if len(excerpt) > 160 else "")
    lines = [f"Draft for note #{note['id']} - {triage.get('category', '')}, {len(body.split())} words",
             f"Your note: \"{excerpt}\""]
    if reference.get("found"):
        lines.append(f"News hook (Google News): {reference.get('headline')} - {reference.get('publisher')}, "
                     f"{reference.get('date')}\n{reference.get('url')}")
    else:
        lines.append("News hook: nothing relevant in Google News, drafted from your note alone.")
    if issues:
        lines.append("Check before posting:\n- " + "\n- ".join(issues))
    lines.append("Not posted anywhere. Reply to the draft with changes, or copy it into LinkedIn when you're happy.")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- schedule

def _now():
    return datetime.now(ZoneInfo(config.TIMEZONE))


def next_slot():
    days = config.DELIVERY_DAYS
    return f"{'/'.join(d.capitalize() for d in days)} {config.DELIVERY_TIME}" if days else "on request only"


def scheduled_delivery(conn):
    if not config.DELIVERY_DAYS:
        return
    now = _now()
    today = now.date().isoformat()
    if now.strftime("%a").lower()[:3] not in config.DELIVERY_DAYS:
        return
    if now.strftime("%H:%M") < config.DELIVERY_TIME or store.kv_get(conn, "last_delivery") == today:
        return
    deliver_next(conn)


def deliver_next(conn):
    """Draft the strongest queued note, at most once per day."""
    today = _now().date().isoformat()
    if store.kv_get(conn, "last_delivery") == today:
        return "already delivered today"
    store.kv_set(conn, "last_delivery", today)
    top = store.queued(conn, limit=1)
    if not top:
        return "queue empty"
    deliver(conn, top[0]["id"])
    return f"drafted note {top[0]['id']}"


# ---------------------------------------------------------------- commands

def _arg_id(args):
    return int(args[0]) if args and args[0].lstrip("#").isdigit() else None


def command(conn, text):
    parts = text.split()
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]
    owner = config.OWNER_CHAT_ID

    if cmd in ("/start", "/help"):
        tg.send(owner, HELP.format(days="/".join(d.capitalize() for d in config.DELIVERY_DAYS) or "request",
                                   time=config.DELIVERY_TIME))
    elif cmd == "/draft":
        note_id = _arg_id(args)
        if note_id is None:
            top = store.queued(conn, limit=1)
            if not top:
                return tg.send(owner, "Nothing queued. Send me a note.")
            note_id = top[0]["id"]
        deliver(conn, note_id)
    elif cmd == "/redo":
        note_id = _arg_id(args)
        if note_id is None:
            return tg.send(owner, "Usage: /redo 12 what to change")
        deliver(conn, note_id, feedback=" ".join(args[1:]) or "Try a different opening and tighten it.")
    elif cmd == "/queue":
        rows = store.queued(conn)
        if not rows:
            return tg.send(owner, "Queue is empty.")
        lines = [f"#{r['id']} ({r['score']}/10) {json.loads(r['triage_json'])['angle']}" for r in rows]
        tg.send(owner, "Queued, strongest first:\n\n" + "\n\n".join(lines))
    elif cmd in ("/keep", "/drop"):
        note_id = _arg_id(args)
        if note_id is None or store.get_note(conn, note_id) is None:
            return tg.send(owner, f"Usage: {cmd} 12")
        store.set_status(conn, note_id, "queued" if cmd == "/keep" else "discarded")
        tg.send(owner, f"#{note_id} {'queued' if cmd == '/keep' else 'dropped'}.")
    elif cmd == "/status":
        c = store.counts(conn)
        tg.send(owner, ", ".join(f"{k}: {v}" for k, v in sorted(c.items())) or "No notes yet.")
    else:
        tg.send(owner, "Unknown command. /help")


# ---------------------------------------------------------------- dispatch

def handle(conn, update):
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    ref = f"{chat_id}:{msg['message_id']}"

    if "channel_post" in update:
        if config.OWNER_CHAT_ID and config.SOURCE_CHANNEL_ID and chat_id == config.SOURCE_CHANNEL_ID:
            text, heard = note_text(msg)
            if text:
                ingest(conn, text, "channel", ref, heard)
        return

    if config.OWNER_CHAT_ID is None:
        print(f"SETUP: message from chat {chat_id} ({msg['chat'].get('username') or msg['chat'].get('first_name')})", flush=True)
        tg.send(chat_id, f"Setup: your chat id is {chat_id}. Put OWNER_CHAT_ID={chat_id} in .env and restart me.")
        return
    if chat_id != config.OWNER_CHAT_ID:
        return  # only Meera's notes
    text, heard = note_text(msg)
    if not text:
        return

    if text.startswith("/"):
        return command(conn, text)

    replied = msg.get("reply_to_message")
    if replied:
        note = store.note_by_ack(conn, replied["message_id"])
        if note is not None:
            if note["status"] == "drafted":
                return deliver(conn, note["id"], feedback=text)
            store.append_to_note(conn, note["id"], text)
            return run_triage(conn, note["id"], heard)

    ingest(conn, text, "dm", ref, heard)


def main():
    config.require("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY")
    conn = store.connect()
    me = tg.get_me()
    print(flush=True); print(f"Running as @{me['username']}. Owner chat: {config.OWNER_CHAT_ID or 'not set - message the bot to get it'}")
    print(f"Voice samples loaded: {len(pipeline.published_pieces())}. Deliveries: {next_slot()} ({config.TIMEZONE})")

    offset = int(store.kv_get(conn, "offset", 0)) or None
    backoff = 5
    while True:
        try:
            updates = tg.get_updates(offset, timeout=30)
            backoff = 5
        except Exception as exc:
            # Only the exception type: the full message contains the bot token in the URL.
            print(f"{datetime.now():%H:%M} Telegram unreachable ({type(exc).__name__}), retrying in {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            store.kv_set(conn, "offset", offset)
            try:
                handle(conn, update)
            except Exception as exc:
                traceback.print_exc()
                if config.OWNER_CHAT_ID:
                    try:
                        tg.send(config.OWNER_CHAT_ID, f"Something failed on my side, your note is saved: {exc}")
                    except Exception:
                        pass
        try:
            if config.OWNER_CHAT_ID:
                scheduled_delivery(conn)
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
