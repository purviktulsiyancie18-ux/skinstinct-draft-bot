# Skinstinct draft bot

Turns Meera's Telegram notes into LinkedIn drafts in her voice, and stops there.

```
Telegram note (typed or voice)
   │
   ▼
0. Voice notes transcribed (Gemini Flash)
   ▼
1. Triage (Gemini Flash, score 1-10) ──> "Not a post" (why)  /  "Holding" (what's missing)
   ▼
2. News hook: Google News RSS headlines -> Gemini Flash picks the single most relevant one
   ▼
3. Draft in her voice (Gemini Pro + voice guide + her 15 published pieces)
   ▼
4. Voice lint -> one automatic rewrite if rules broken
   ▼
Draft + review card sent straight to her private chat.  END.
```

**The Cut:** there is no LinkedIn integration and no scheduling anywhere in the code. A draft only ever goes to Meera's private chat. Publishing is her call and her hands.

**No queue, no storage.** Each note is handled start to finish as soon as it arrives. Replies work because Telegram includes the message being replied to.

## What Meera does in Telegram

| She does | Bot does |
|---|---|
| Sends or forwards a note, typed or voice, or posts it in her notes channel | If there's a post in it: "Worth a post (8/10)...", then the draft about a minute later. If not: "Not a post" or "Holding", with the reason |
| Replies to a draft: "shorter, open with the Kochi data" | Sends a revised draft |
| Replies to "Holding" / "Not a post" with more detail | Re-checks the note with the detail added |
| Replies "draft it" to "Holding" / "Not a post" | Drafts it anyway. Her judgement wins |

Each draft comes with a review card: the Google News headline used plus its link, and anything to check, such as `[CHECK: ...]` placeholders. The model writes these instead of inventing Skinstinct numbers, customer stories or dates. The draft only uses what the headline itself says, and links come straight from the Google News feed, never from the model.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add TELEGRAM_BOT_TOKEN and GEMINI_API_KEY
.venv/bin/python bot.py     # long polling
```

Message the bot once. It replies with your chat id. Set `OWNER_CHAT_ID` and restart.

Try one note without Telegram:

```bash
.venv/bin/python pipeline.py "Customer asked if our serum works with her 15% vit C. Other brand doesn't publish pH."
```

Triage report for an old backlog (Telegram Desktop -> Export chat history -> JSON):

```bash
.venv/bin/python import_backlog.py result.json
```

## Host on Vercel

`app.py` (Flask) is the entry point. Telegram posts each update to `/api/webhook`. No database needed.

1. Import the GitHub repo in Vercel.
2. **Settings → Environment Variables:** paste your `.env`. You need `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OWNER_CHAT_ID`, `SOURCE_CHANNEL_ID` and `TELEGRAM_WEBHOOK_SECRET`. Then redeploy.
3. Stop any local `python bot.py`, then set the webhook in a browser:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<project>.vercel.app/api/webhook&secret_token=<TELEGRAM_WEBHOOK_SECRET>
```

Without `secret_token`, every update is rejected with 403. Check with `.../bot<TOKEN>/getWebhookInfo`. Or run `.venv/bin/python set_webhook.py https://<project>.vercel.app`, and use `--delete` to go back to local polling.

A draft takes 30-60 seconds and Vercel functions allow up to 300s. If Telegram redelivers a note while it is still drafting, a warm instance ignores the repeat. Rarely, a cold one may draft it twice.

## Files

- `app.py`: Vercel webhook entry point
- `bot.py`: message handling (shared by webhook and local polling)
- `pipeline.py`: transcription, triage, Google News hook, draft and revise prompts, voice lint
- `telegram_api.py`: minimal Bot API client
- `import_backlog.py`: triage report for old fragments
- `voice/published/`: her 15 published pieces, extracted from the seed data PDF. This is the only voice ground truth. Add new published posts here and they are picked up automatically.
- `Meera_Pillai_Voice_Guide.txt`: the style guide, included in every prompt
