"""Vercel entry point: the same bot, driven by a Telegram webhook instead of polling.

    POST /api/webhook    Telegram delivers each update here (/telegram also works) (checked against TELEGRAM_WEBHOOK_SECRET)
    GET  /cron/deliver   Vercel Cron, Mon/Wed/Fri 08:30 IST (checked against CRON_SECRET)
    GET  /               health check

Locally you can still run `python bot.py` (polling) instead - but not both at once.
"""
import hmac
import traceback

from flask import Flask, jsonify, request

import bot
import config
import store

app = Flask(__name__)


def _secret_ok(given, expected):
    return bool(expected) and hmac.compare_digest(given or "", expected)


@app.get("/")
def health():
    return jsonify(ok=True, service="skinstinct-draft-bot",
                   owner_configured=bool(config.OWNER_CHAT_ID),
                   database="postgres" if store.PG_URL else "sqlite (temporary - attach Postgres)")


@app.post("/api/webhook")
@app.post("/telegram")
def telegram_webhook():
    if not _secret_ok(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), config.TELEGRAM_WEBHOOK_SECRET):
        return "forbidden", 403
    update = request.get_json(silent=True) or {}
    conn = store.connect()
    try:
        # Telegram retries if a draft takes a while; handle each update once.
        if "update_id" in update and store.claim(conn, f"update:{update['update_id']}"):
            bot.handle(conn, update)
    except Exception as exc:
        traceback.print_exc()
        if config.OWNER_CHAT_ID:
            try:
                bot.tg.send(config.OWNER_CHAT_ID, f"Something failed on my side, your note is saved: {exc}")
            except Exception:
                pass
    finally:
        conn.close()
    return "ok"  # always 200, otherwise Telegram keeps redelivering


@app.get("/cron/deliver")
def cron_deliver():
    if not _secret_ok(request.headers.get("Authorization", "").removeprefix("Bearer "), config.CRON_SECRET):
        return "forbidden", 403
    conn = store.connect()
    try:
        return jsonify(result=bot.deliver_next(conn))
    finally:
        conn.close()
