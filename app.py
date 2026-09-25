"""Vercel entry point: the same bot, driven by a Telegram webhook instead of polling.

    POST /api/webhook    Telegram delivers each update here (checked against TELEGRAM_WEBHOOK_SECRET)
    GET  /               health check (also repairs the Telegram webhook if it's wrong)

No storage: each note is triaged and drafted within the request.
"""
import hmac
import os
import time
from collections import deque

from flask import Flask, jsonify, request

import bot
import config
import telegram_api as tg

app = Flask(__name__)

# Telegram may redeliver an update while a slow draft is still running. A warm
# instance remembers recent update ids so it doesn't draft the same note twice.
_recent = deque(maxlen=500)


REQUIRED_ENV = ["TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OWNER_CHAT_ID",
                "SOURCE_CHANNEL_ID", "TELEGRAM_WEBHOOK_SECRET"]


def _safe_ensure():
    try:
        return ensure_webhook()
    except Exception as exc:
        return f"check failed ({type(exc).__name__})"


@app.get("/")
def health():
    # Names only, never values - so a missing setting can be spotted from the browser.
    return jsonify(ok=True, service="skinstinct-draft-bot",
                   owner_configured=bool(config.OWNER_CHAT_ID),
                   webhook_secret_configured=bool(config.TELEGRAM_WEBHOOK_SECRET),
                   env_missing=[k for k in REQUIRED_ENV if not os.getenv(k, "").strip()],
                   telegram_webhook=_safe_ensure(),
                   vercel_env=os.getenv("VERCEL_ENV"),
                   commit=(os.getenv("VERCEL_GIT_COMMIT_SHA") or "")[:7])


# --------------------------------------------------------------- self-healing webhook
# If someone re-runs a setWebhook link without secret_token (or with a typo'd URL),
# Telegram's requests arrive unsigned and the bot goes silent. Instead, the bot
# re-registers its own webhook - correct https URL + secret - and Telegram's retry
# of the same update then goes through.

_last_heal = [0.0]


def webhook_url():
    host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL")  # set by Vercel, e.g. xyz.vercel.app
    return f"https://{host}/api/webhook" if host else None


def ensure_webhook(force=False):
    """Re-register the webhook if it's wrong. Returns a short status string."""
    url = webhook_url()
    if not (url and config.TELEGRAM_WEBHOOK_SECRET and config.TELEGRAM_BOT_TOKEN):
        return "cannot check (not on Vercel or settings missing)"
    current = tg._call("getWebhookInfo").get("url", "")
    if current == url:
        if not force:
            return "ok"
        # URL looks right but updates arrive unsigned (secret missing). Throttle so
        # a stream of unsigned requests can't hammer setWebhook; a wrong URL is
        # always fixed immediately.
        if time.time() - _last_heal[0] < 30:
            return "repair already in progress"
    _last_heal[0] = time.time()
    tg._call("setWebhook", url=url, secret_token=config.TELEGRAM_WEBHOOK_SECRET,
             allowed_updates=["message", "channel_post"])
    print(f"webhook repaired: was {current!r}, now {url}", flush=True)
    return f"repaired (was {current or 'not set'})"


@app.post("/api/webhook")
def telegram_webhook():
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if given is None and config.TELEGRAM_WEBHOOK_SECRET:
        # Unsigned: the webhook was registered without the secret. Fix it, and let
        # Telegram retry this update (a non-200 makes it redeliver, now signed).
        try:
            ensure_webhook(force=True)
        except Exception as exc:
            print("webhook repair failed:", type(exc).__name__, flush=True)
        return "webhook re-registered, retry", 503
    if not config.TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(given or "", config.TELEGRAM_WEBHOOK_SECRET):
        return "forbidden", 403
    update = request.get_json(silent=True) or {}
    update_id = update.get("update_id")
    if update_id in _recent:
        return "ok"
    _recent.append(update_id)
    bot.safe_handle(update)
    return "ok"  # always 200, otherwise Telegram keeps redelivering
