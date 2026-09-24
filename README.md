# Skinstinct draft bot

Turns Meera's Telegram notes into LinkedIn drafts in her voice, and stops there.

```
Telegram note (typed or voice) ──> 0. Voice notes transcribed (Gemini Flash)
                     ▼
                  1. Triage (Gemini Flash, score 1-10) ──> discard (tells her why, /keep to overrule)
                     │      └─> hold (asks for the one missing detail)
                     ▼
                  queued (strongest first)
                     │  Mon/Wed/Fri 08:30, or /draft
                     ▼
                  2. News hook: Google News RSS headlines -> Gemini Flash picks the single most relevant one
                     ▼
                  3. Draft in her voice (Gemini Pro + voice guide + her 15 published pieces)
                     ▼
                  4. Voice lint -> one automatic rewrite if rules broken
                     ▼
                  Draft + review card sent to her chat.  END.
```

**The Cut:** there is no LinkedIn integration and no post scheduling anywhere in the code. The only place a draft goes is Meera's private chat. Publishing is her call and her hands.

## Setup

```bash
cd "Telegram LinkedIn Automation"
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add TELEGRAM_BOT_TOKEN and GEMINI_API_KEY
.venv/bin/python bot.py
```

Then message the bot. It replies with your chat id. Put it in `.env` as `OWNER_CHAT_ID` and restart. From then on it only listens to that chat (plus `SOURCE_CHANNEL_ID`, if set and the bot is an admin there).

Try the pipeline on one note without Telegram:

```bash
.venv/bin/python pipeline.py "Customer asked if our serum works with her 15% vit C. Couldn't answer - other brand doesn't publish pH."
```

Triage the existing backlog (Telegram Desktop -> channel -> Export chat history -> JSON):

```bash
.venv/bin/python import_backlog.py result.json --dry-run
```

## What Meera does in Telegram

| She does | Bot does |
|---|---|
| Sends or forwards a note, typed or voice | Replies in one line: queued / holding (what's missing) / not a post (why). Voice notes are echoed back as "Heard: ..." so she can spot a bad transcription |
| Replies to a "holding" message | Adds the detail and re-checks it |
| Nothing | Mon/Wed/Fri 08:30: the strongest queued note becomes a draft |
| `/draft` or `/draft 12` | Drafts now |
| Replies to a draft with "shorter, open with the Kochi return data" | Redrafts using that note, same reference |
| `/queue`, `/keep 12`, `/drop 12`, `/status` | Queue management |

Each draft comes with a review card: the source note, the Google News headline used plus its link, and anything to check, such as `[CHECK: ...]` placeholders. The model writes these instead of inventing Skinstinct numbers, customer stories or dates.

The draft only uses what the headline itself says. Links come straight from the Google News feed, never from the model, so they can't be made up.

## Files

- `bot.py`: Telegram loop, commands, delivery schedule
- `pipeline.py`: triage, reference and draft prompts, plus the voice lint
- `store.py`: SQLite (notes, drafts, every version kept)
- `import_backlog.py`: one-off triage of old fragments
- `voice/published/`: her 15 published pieces, extracted from the seed data PDF. This is the only voice ground truth. Add new published posts here and they are picked up automatically.
- `Meera_Pillai_Voice_Guide.txt`: the style guide, included in every prompt

## Why drafts are made at delivery time, not when the note arrives

The news hook has to be current when she reads the draft. A note queued for a week gets its headline found on the day it is drafted, not the day it was captured.

## Hosting on Vercel

`app.py` is the Vercel entry point. It runs the same bot through a Telegram webhook (`POST /telegram`) and a Vercel Cron job (`/cron/deliver`, Mon/Wed/Fri 03:00 UTC = 08:30 IST) instead of the polling loop.

1. Import the repo in Vercel (framework preset: Other). `requirements.txt` is picked up automatically.
2. **Storage → Create Database → Neon (Postgres)** and connect it to the project. This sets `DATABASE_URL`. Without it, notes live in `/tmp` and are lost between requests.
3. **Settings → Environment Variables:** paste the contents of your local `.env`. Vercel splits it into variables for you. You need `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OWNER_CHAT_ID`, `SOURCE_CHANNEL_ID`, `TELEGRAM_WEBHOOK_SECRET` and `CRON_SECRET`.
4. Redeploy, open `https://<project>.vercel.app/` and check that it reports `"database": "postgres"`.
5. Stop any local `python bot.py`, then point Telegram at Vercel:

```bash
.venv/bin/python set_webhook.py https://<project>.vercel.app
```

To go back to running locally: `set_webhook.py --delete`, then `python bot.py`.
