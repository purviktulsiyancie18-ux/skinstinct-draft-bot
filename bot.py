"""Skinstinct draft bot - Telegram in, draft for Meera's review out.

Stateless: every note is handled start to finish in one go -
(transcribe) -> triage -> Google News hook -> draft -> sent to Meera's chat.
Nothing is stored; replies work because Telegram includes the message being
replied to. It never publishes anything.

    .venv/bin/python bot.py      # local long polling (Vercel uses app.py instead)
"""
import time
import traceback
from datetime import datetime

import config
import pipeline
import telegram_api as tg

HELP = """I turn your notes into LinkedIn drafts for you to review. I never post anything.

Send or forward me a note - typed or a voice note - or post it in your notes channel. If there's a post in it, the draft arrives here in about a minute. If not, I'll tell you why.

Reply to a draft with changes ("shorter", "open with the Kochi data") and I'll revise it.
Reply to a "Holding" or "Not a post" message with more detail and I'll re-check it, or reply "draft it" to overrule me."""

# Prefixes of the bot's own messages, so a reply can be understood without storage.
HOLDING, NOT_A_POST, WORKING, CARD = "Holding:", "Not a post:", "Worth a post", "Draft ready"
NOTE_MARKER = "\n\nYour note:\n"
FORCE_WORDS = {"draft it", "draft", "draft anyway", "/draft", "do it", "go"}


def owner_send(text):
    return tg.send(config.OWNER_CHAT_ID, text)


# ---------------------------------------------------------------- the pipeline

def note_text(msg):
    """Text of a message; voice notes and audio files are transcribed by Gemini.
    Returns (text, was_voice)."""
    text = (msg.get("text") or msg.get("caption") or "").strip()
    media = msg.get("voice") or msg.get("audio") or msg.get("video_note")
    if not media:
        return text, False
    tg.typing(config.OWNER_CHAT_ID)
    mime = media.get("mime_type") or ("video/mp4" if "video_note" in msg else "audio/ogg")
    spoken = pipeline.transcribe(tg.download(media["file_id"]), mime)
    return "\n\n".join(t for t in (text, spoken) if t), True


def process(note, heard=False, force=False):
    tg.typing(config.OWNER_CHAT_ID)
    t = pipeline.triage(note)
    heard_line = f"Heard: \"{note}\"\n\n" if heard else ""

    if not force and t.verdict == "hold":
        return owner_send(f"{HOLDING} {t.reason}\n\nTo make it a post: {t.missing}\n"
                          f"Reply to this message with that and I'll re-check, or reply \"draft it\"."
                          f"{NOTE_MARKER}{note}")
    if not force and t.verdict == "discard":
        return owner_send(f"{NOT_A_POST} {t.reason}\n\nReply \"draft it\" if you disagree, "
                          f"or reply with more detail and I'll re-check.{NOTE_MARKER}{note}")

    owner_send(f"{heard_line}{WORKING} ({t.score}/10, {t.category}): {t.angle}\n\nDrafting now - about a minute.")
    tg.typing(config.OWNER_CHAT_ID)
    reference = pipeline.find_reference(t.angle, t.search_queries)
    tg.typing(config.OWNER_CHAT_ID)
    body, issues = pipeline.draft_post(note, t.model_dump(), reference)
    owner_send(body)
    owner_send(review_card(t.category, reference, body, issues))


def revise(previous, feedback):
    tg.typing(config.OWNER_CHAT_ID)
    body, issues = pipeline.revise_draft(previous, feedback)
    owner_send(body)
    owner_send(review_card(None, None, body, issues, revised=True))


def review_card(category, reference, body, issues, revised=False):
    lines = [f"{CARD}{' (revised)' if revised else ''}{' - ' + category if category else ''}, {len(body.split())} words"]
    if reference is not None:
        if reference.get("found"):
            lines.append(f"News hook (Google News): {reference.get('headline')} - {reference.get('publisher')}, "
                         f"{reference.get('date')}\n{reference.get('url')}")
        else:
            lines.append("News hook: nothing relevant in Google News, drafted from your note alone.")
    if issues:
        lines.append("Check before posting:\n- " + "\n- ".join(issues))
    lines.append("Not posted anywhere. Reply to the draft above with changes, or copy it into LinkedIn when you're happy.")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- dispatch

def handle_reply(text, replied, heard):
    """Meera replied to one of the bot's messages. Returns True if handled."""
    if not (replied.get("from") or {}).get("is_bot"):
        return False
    original = replied.get("text") or ""
    if original.startswith((HOLDING, NOT_A_POST)) and NOTE_MARKER in original:
        note = original.split(NOTE_MARKER, 1)[1]
        if text.strip().lower().rstrip(".!") in FORCE_WORDS:
            process(note, force=True)
        else:
            process(f"{note}\n\n{text}", heard=heard)
        return True
    if original.startswith(CARD):
        owner_send("Reply to the draft itself (the message above this one) and I'll revise it.")
        return True
    if original.startswith((WORKING, "Heard:", "I turn your notes", "Something failed")):
        return False  # not a draft - treat the reply as a new note
    revise(original, text)  # anything else the bot sent is a draft
    return True


def handle(update):
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        return
    chat_id = msg["chat"]["id"]

    if "channel_post" in update:
        if config.OWNER_CHAT_ID and config.SOURCE_CHANNEL_ID and chat_id == config.SOURCE_CHANNEL_ID:
            text, heard = note_text(msg)
            if text:
                process(text, heard)
        return

    if config.OWNER_CHAT_ID is None:
        print(f"SETUP: message from chat {chat_id}", flush=True)
        tg.send(chat_id, f"Setup: your chat id is {chat_id}. Set OWNER_CHAT_ID={chat_id} and restart me.")
        return
    if chat_id != config.OWNER_CHAT_ID:
        return  # only Meera's notes

    text, heard = note_text(msg)
    if not text:
        return
    if text.startswith("/"):
        owner_send(HELP)
        return
    replied = msg.get("reply_to_message")
    if replied and handle_reply(text, replied, heard):
        return
    process(text, heard)


def safe_handle(update):
    try:
        handle(update)
    except Exception as exc:
        traceback.print_exc()
        if config.OWNER_CHAT_ID:
            try:
                owner_send(f"Something failed on my side ({type(exc).__name__}: {exc}). Send the note again in a minute.")
            except Exception:
                pass


def main():
    config.require("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY")
    me = tg.get_me()
    print(f"Running as @{me['username']}. Owner chat: {config.OWNER_CHAT_ID or 'not set - message the bot to get it'}", flush=True)
    offset, backoff = None, 5
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
            safe_handle(update)


if __name__ == "__main__":
    main()
