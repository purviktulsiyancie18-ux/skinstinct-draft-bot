"""Vercel entry point: the same bot, driven by a Telegram webhook instead of polling.

    POST /api/webhook    Telegram delivers each update here (checked against TELEGRAM_WEBHOOK_SECRET)
    GET  /               health check

No storage: each note is triaged and drafted within the request.
"""
import hmac
from collections import deque

from flask import Flask, jsonify, request

import bot
import config

app = Flask(__name__)

# Telegram may redeliver an update while a slow draft is still running. A warm
# instance remembers recent update ids so it doesn't draft the same note twice.
_recent = deque(maxlen=500)


@app.get("/")
def health():
    return jsonify(ok=True, service="skinstinct-draft-bot",
                   owner_configured=bool(config.OWNER_CHAT_ID),
                   webhook_secret_configured=bool(config.TELEGRAM_WEBHOOK_SECRET))


@app.post("/api/webhook")
def telegram_webhook():
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token") or ""
    if not config.TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(given, config.TELEGRAM_WEBHOOK_SECRET):
        return "forbidden", 403
    update = request.get_json(silent=True) or {}
    update_id = update.get("update_id")
    if update_id in _recent:
        return "ok"
    _recent.append(update_id)
    bot.safe_handle(update)
    return "ok"  # always 200, otherwise Telegram keeps redelivering
