"""Minimal Telegram Bot API client (long polling). Read and reply only."""
import requests

import config

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's hard limit is 4096


def _call(method, **params):
    try:
        resp = requests.post(API.format(token=config.TELEGRAM_BOT_TOKEN, method=method), json=params, timeout=70)
    except requests.RequestException as exc:
        # requests puts the full URL - including the bot token - in its messages
        raise RuntimeError(f"Telegram {method}: network error ({type(exc).__name__})") from None
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data["result"]


def get_me():
    return _call("getMe")


def get_updates(offset=None, timeout=50):
    return _call("getUpdates", offset=offset, timeout=timeout,
                 allowed_updates=["message", "channel_post"])


def download(file_id):
    """Bytes of a file Meera sent (voice notes are well under the 20 MB bot limit)."""
    path = _call("getFile", file_id=file_id)["file_path"]
    try:
        resp = requests.get(f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{path}", timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram download: {type(exc).__name__}") from None
    return resp.content


def _chunks(text):
    """Split on paragraph boundaries so a long post never breaks mid-sentence."""
    if len(text) <= MAX_LEN:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= MAX_LEN:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = para[:MAX_LEN]
    if current:
        chunks.append(current)
    return chunks


def send(chat_id, text, reply_to=None):
    """Plain text (no parse_mode) so her punctuation never gets mangled. Returns first message id."""
    first_id = None
    for i, chunk in enumerate(_chunks(text)):
        params = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if reply_to and i == 0:
            params["reply_to_message_id"] = reply_to
            params["allow_sending_without_reply"] = True
        msg = _call("sendMessage", **params)
        first_id = first_id or msg["message_id"]
    return first_id


def typing(chat_id):
    try:
        _call("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass
