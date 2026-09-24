"""Point Telegram at the Vercel deployment (run once, locally, after deploying).

    .venv/bin/python set_webhook.py https://your-project.vercel.app
    .venv/bin/python set_webhook.py --delete      # back to local polling (python bot.py)
"""
import sys

import config
import telegram_api as tg


def main():
    config.require("TELEGRAM_BOT_TOKEN")
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == "--delete":
        tg._call("deleteWebhook")
        print("Webhook removed. Run `python bot.py` to poll locally.")
        return
    config.require("TELEGRAM_WEBHOOK_SECRET")
    url = sys.argv[1].rstrip("/") + "/telegram"
    tg._call("setWebhook", url=url, secret_token=config.TELEGRAM_WEBHOOK_SECRET,
             allowed_updates=["message", "channel_post"], drop_pending_updates=False)
    info = tg._call("getWebhookInfo")
    print(f"Webhook set: {info['url']}  pending={info.get('pending_update_count', 0)}")
    if info.get("last_error_message"):
        print("Last error from Telegram:", info["last_error_message"])


if __name__ == "__main__":
    main()
