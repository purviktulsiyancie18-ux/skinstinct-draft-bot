"""The model steps - transcribe, triage, news hook, draft - plus a voice lint.

Deliberately stops at a draft. There is no LinkedIn publishing or post-scheduling code
anywhere in this project, by design (Meera's "Cut").

Try a single note without Telegram:
    .venv/bin/python pipeline.py "note text here"
"""
import json
import re
import sys
from functools import lru_cache
from typing import List, Literal

import requests

import config  # first: it silences library warnings before they import

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Voice context: the guide + her 15 published pieces (the only ground truth)
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def published_pieces():
    return {p.stem: p.read_text().strip() for p in sorted(config.PUBLISHED_DIR.glob("*.txt"))}


@lru_cache(maxsize=1)
def voice_context():
    guide = config.VOICE_GUIDE_PATH.read_text()
    pieces = published_pieces()
    linkedin = "\n\n".join(f"<<{k}>>\n{v}" for k, v in pieces.items() if k.startswith("linkedin"))
    letters = "\n\n".join(f"<<{k}>>\n{v}" for k, v in pieces.items() if not k.startswith("linkedin"))
    return f"""# VOICE GUIDE (derived from her published work)
{guide}

# HER 4 PUBLISHED LINKEDIN POSTS - the primary reference for format, length and register
{linkedin}

# HER 11 PUBLISHED NEWSLETTERS - reference for voice, beliefs and phrasing only.
# Do NOT copy the newsletter format ("Hi," / "Meera") into a LinkedIn post.
{letters}
"""


@lru_cache(maxsize=1)
def client():
    config.require("GEMINI_API_KEY")
    return genai.Client(api_key=config.GEMINI_API_KEY)


# --------------------------------------------------------------------------
# Step 0 - Voice note to text (Gemini Flash)
# --------------------------------------------------------------------------

TRANSCRIBE_PROMPT = """Transcribe this voice note from Meera Pillai, a skincare founder, into text.
Keep her words and meaning; drop filler (um, uh, repeated false starts) only.
Do not summarise, rephrase or add anything. Spell technical terms correctly
(niacinamide, ceramides, CoA, INCI, pH, CDSCO) and use British spelling. If she
switches language, write that part in English. Return only the transcript."""


def transcribe(audio, mime_type="audio/ogg"):
    resp = client().models.generate_content(
        model=config.TRIAGE_MODEL,
        contents=[types.Part.from_bytes(data=audio, mime_type=mime_type), TRANSCRIBE_PROMPT],
        config=types.GenerateContentConfig(temperature=0),
    )
    return (resp.text or "").strip()


# --------------------------------------------------------------------------
# Step 1 - Triage: is there a post in this fragment at all?
# --------------------------------------------------------------------------

class Triage(BaseModel):
    verdict: Literal["develop", "hold", "discard"]
    score: int = Field(description="1-10: how strong a LinkedIn post this could become in her voice")
    reason: str = Field(description="One plain sentence explaining the verdict, addressed to Meera")
    angle: str = Field(description="If develop/hold: the gap between belief and reality the post would explain, one sentence")
    category: str = Field(description="Ingredient Deep-Dive | Founder Story | India-Specific Context | Industry Transparency | Formulation Science | Consumer Education | Brand Philosophy")
    missing: str = Field(description="If hold: the one specific thing she'd need to add (a number, the date, what happened). Else empty")
    search_queries: List[str] = Field(description="If develop: 2-3 short Google News queries of 2-5 keywords, from specific to broad (e.g. 'SPF labelling India', 'sunscreen regulation India', 'Indian skincare market'), to find a current news hook. Else empty list")


TRIAGE_PROMPT = """You are the editorial filter for Meera Pillai, founder of Skinstinct. She drops raw
fragments into Telegram. Most will never be posts. Your job is to be a strict,
honest first reader so she only spends review time on drafts worth publishing.

Use the voice guide and her published work (in your instructions) to judge fit.

A fragment is DEVELOP only if it contains, or clearly points to, something that
can carry a 450-650 word LinkedIn post in her voice:
- a specific observation, number, scene, customer question, or formulation fact;
- a real gap between what people believe (or what labels say) and what is true;
- something she can explain from formulation/process knowledge without making
  medical claims or speaking as a dermatologist;
- useful to readers even if Skinstinct did not exist.

HOLD if the core is promising but it's too thin to draft without inventing the
substance (e.g. "that thing the CM said about pH" with no detail). Name exactly
what's missing in `missing`.

DISCARD if it is: a to-do or logistics note; a pure feeling with no insight; a
sales, launch or discount idea; a hot take requiring medical authority; an attack
on a named competitor or person; a duplicate of what her published pieces already
say with nothing new; or otherwise not publishable in any form.

Be conservative: a weak draft costs her more time than no draft. Scores of 8+
should be rare and reserved for fragments with a concrete hook and a clear gap.

FRAGMENT (received {received}):
\"\"\"{note}\"\"\"
"""


def triage(note_text, received="recently"):
    resp = client().models.generate_content(
        model=config.TRIAGE_MODEL,
        contents=TRIAGE_PROMPT.format(note=note_text, received=received),
        config=types.GenerateContentConfig(
            system_instruction=voice_context(),
            response_mime_type="application/json",
            response_schema=Triage,
            temperature=0.2,
        ),
    )
    result = resp.parsed or Triage.model_validate_json(resp.text)
    if result.verdict == "develop" and result.score < config.MIN_SCORE:
        result.verdict = "hold" if result.score >= config.MIN_SCORE - 2 else "discard"
    return result


# --------------------------------------------------------------------------
# Step 2 - One current news hook from Google News, picked by Gemini Flash
# --------------------------------------------------------------------------

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def google_news(query, window="1y", limit=15):
    """Headlines from Google News RSS (India edition). Links come from the feed, never from the model."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    q = f"{query} when:{window}" if window else query
    try:
        resp = requests.get(GOOGLE_NEWS_RSS, timeout=15,
                            params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
        root = ET.fromstring(resp.content)
    except Exception:
        return []
    items = []
    for item in root.iter("item"):
        source = item.find("source")
        title = (item.findtext("title") or "").strip()
        publisher = source.text.strip() if source is not None and source.text else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        try:
            date = parsedate_to_datetime(item.findtext("pubDate")).strftime("%d %b %Y")
        except Exception:
            date = ""
        items.append({"headline": title, "publisher": publisher, "date": date,
                      "url": item.findtext("link") or ""})
        if len(items) >= limit:
            break
    return items


class NewsPick(BaseModel):
    index: int = Field(description="Index of the single most relevant headline, or -1 if none is genuinely relevant")
    key_fact: str = Field(description="What the headline itself establishes, without adding anything it doesn't say")
    relevance: str = Field(description="One sentence on how it connects to the angle")


PICK_PROMPT = """Pick ONE news headline that would give a LinkedIn post by an Indian skincare
founder (formulation background) a current hook. Today is {today}.

ANGLE OF THE POST: {angle}

Prefer: Indian regulation/enforcement (CDSCO, BIS, ASCI), credible research,
industry or market data, then mainstream skincare-market news. Prefer recent
over old. Ignore press-release spam, market-size reports from unknown research
firms, celebrity or product-launch pieces. If nothing is genuinely relevant to
the angle, return -1 - a forced hook is worse than none.

The post may only use what the headline states, so `key_fact` must not add
details the headline doesn't contain.

HEADLINES:
{headlines}
"""


def find_reference(angle, queries):
    from datetime import date
    if isinstance(queries, str):
        queries = [queries]
    queries = [q for q in (queries or []) if q] or [" ".join(angle.split()[:5])]
    candidates, seen = [], set()
    for q in queries[:3]:  # pool specific and broad results, let the model choose
        for item in google_news(q, limit=12):
            if item["url"] not in seen and item["headline"] not in seen:
                seen.update((item["url"], item["headline"]))
                candidates.append(item)
    if not candidates:
        return {"found": False, "error": "Google News returned nothing"}

    listing = "\n".join(f"[{i}] {c['headline']} - {c['publisher']}, {c['date']}" for i, c in enumerate(candidates))
    try:
        resp = client().models.generate_content(
            model=config.RESEARCH_MODEL,
            contents=PICK_PROMPT.format(angle=angle, today=date.today().isoformat(), headlines=listing),
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=NewsPick, temperature=0.2),
        )
        pick = resp.parsed or NewsPick.model_validate_json(resp.text)
    except Exception as exc:  # a missing hook should never block a draft
        return {"found": False, "error": str(exc)}
    if not 0 <= pick.index < len(candidates):
        return {"found": False, "error": "no relevant headline in Google News"}
    return {"found": True, "source": "Google News", **candidates[pick.index],
            "key_fact": pick.key_fact, "relevance": pick.relevance, "url_ok": True}


# --------------------------------------------------------------------------
# Step 3 - Draft in her voice
# --------------------------------------------------------------------------

DRAFT_PROMPT = """Write a LinkedIn post as Meera Pillai, from her raw note below. It is a first
draft for her to review - she will edit and publish it herself.

HER RAW NOTE:
\"\"\"{note}\"\"\"

EDITORIAL ANGLE: {angle}
CATEGORY: {category}

CURRENT NEWS HOOK (weave in naturally, at most once, attributed plainly in prose
e.g. "Last week the Economic Times reported that ..." - never as a link dump).
Use only what the headline states; do not add figures or details it doesn't give:
{reference}

HARD RULES
1. Voice: match her published LinkedIn posts in your instructions - cold open
   with a concrete number, dated scene, customer question or direct claim; state
   the gap; explain the mechanism plainly in 2-3 named factors; one explicit
   "I'm not saying X. I'm saying Y." boundary; Skinstinct only late, specific and
   non-salesy, with stake disclosed where relevant; end with a question readers
   can ask any brand, a transparency note, or a quiet forward promise.
2. Format: 450-650 words. Full prose paragraphs of 3-6 sentences (one short
   standalone line allowed). No greeting, no sign-off, no hashtags, no emojis, no
   exclamation marks, no bullets or numbered lists, no bold, no headers. Use a
   spaced hyphen " - " as the dash, never an em dash. British spelling.
3. Truth: every Skinstinct-specific fact (numbers, dates, customer stories,
   test results, what they sell) must come from her note. Every external number
   must come from the reference above or be well-established formulation science.
   If the post genuinely needs a detail she hasn't given, write a placeholder
   like [CHECK: our return rate for this product] rather than inventing it.
4. Originality: do not reuse sentences from her published pieces. Same voice,
   new words.
{feedback}
Return only the post text."""


def _format_reference(ref):
    if not ref or not ref.get("found"):
        return "(none found - write from the note alone, do not invent a current event)"
    return (f"Headline: {ref.get('headline')} - {ref.get('publisher')}, {ref.get('date')}\n"
            f"Key fact: {ref.get('key_fact')}\nRelevance: {ref.get('relevance')}")


def draft_post(note_text, triage_result, reference, feedback=None, previous=None):
    fb = ""
    if feedback:
        fb = f"\nMEERA'S FEEDBACK ON THE PREVIOUS DRAFT (apply it):\n{feedback}\n"
        if previous:
            fb += f"\nPREVIOUS DRAFT:\n\"\"\"{previous}\"\"\"\n"
    prompt = DRAFT_PROMPT.format(
        note=note_text,
        angle=triage_result.get("angle", ""),
        category=triage_result.get("category", ""),
        reference=_format_reference(reference),
        feedback=fb,
    )
    body = _generate_text(prompt)

    issues = lint(body)
    if [i for i in issues if not i.startswith(SOFT)]:
        body = _generate_text(
            prompt + "\n\nYOUR PREVIOUS ATTEMPT:\n\"\"\"" + body + "\"\"\"\n\nIt broke these rules; "
            "fix them and change nothing else:\n- " + "\n- ".join(issues)
        )
    body = autofix(body)
    return body, lint(body)


def _generate_text(prompt):
    resp = client().models.generate_content(
        model=config.DRAFT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=voice_context(), temperature=0.7),
    )
    return (resp.text or "").strip()


# --------------------------------------------------------------------------
# Voice lint - cheap deterministic checks from the guide's self-check list
# --------------------------------------------------------------------------

BANNED = ["game-changer", "game changer", "holy grail", "miracle", "glow up", "obsessed",
          "must-have", "revolutionary", "chemical-free", "magic", "hero ingredient",
          "you deserve", "treat yourself", "limited time", "hurry", "amazing",
          "incredible", "luxe", "secret", "link in bio"]

AMERICAN = {"color": "colour", "moisturizer": "moisturiser", "oxidize": "oxidise",
            "oxidizes": "oxidises", "oxidized": "oxidised", "sensitization": "sensitisation",
            "standardized": "standardised", "characterized": "characterised",
            "organization": "organisation", "optimize": "optimise", "optimized": "optimised",
            "minimize": "minimise", "maximize": "maximise", "realize": "realise",
            "recognize": "recognise", "analyze": "analyse", "behavior": "behaviour",
            "favorite": "favourite", "stabilize": "stabilise", "stabilized": "stabilised",
            "formulated for fall": "formulated for autumn"}

# Reported to Meera but not worth an automatic rewrite (one of her own posts has no boundary line).
SOFT = ("placeholder", "no explicit")

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅]")


def lint(text):
    issues = []
    words = len(text.split())
    if not 420 <= words <= 700:
        issues.append(f"length is {words} words (target 450-650)")
    if "!" in text:
        issues.append("contains an exclamation mark")
    if EMOJI.search(text):
        issues.append("contains an emoji")
    if re.search(r"(^|\s)#\w", text):
        issues.append("contains a hashtag")
    if re.search(r"^\s*([-*•]|\d+[.)])\s", text, re.M):
        issues.append("contains a bulleted or numbered list")
    if "**" in text or re.search(r"^#+\s", text, re.M):
        issues.append("contains markdown bold or headers")
    if "—" in text or "–" in text:
        issues.append("uses an em/en dash instead of ' - '")
    lower = text.lower()
    for w in BANNED:
        if re.search(r"\b" + re.escape(w) + r"\b", lower):
            issues.append(f"uses banned word '{w}'")
    for us, uk in AMERICAN.items():
        if re.search(r"\b" + us + r"\b", lower):
            issues.append(f"American spelling '{us}' (use '{uk}')")
    if re.match(r"\s*(hi|hello|hey)\b", lower) or re.search(r"\n\s*(meera|- ?meera)\s*$", lower):
        issues.append("has a newsletter greeting or sign-off")
    first_para = text.split("\n\n")[0].lower()
    if "skinstinct" in first_para:
        issues.append("mentions Skinstinct in the opening paragraph (brand should come late)")
    if "not saying" not in lower and "not making a case" not in lower and "not trying to" not in lower:
        issues.append("no explicit 'I'm not saying X. I'm saying Y.' boundary")
    copied = _copied_phrase(text)
    if copied:
        issues.append(f"reuses a sentence from a published piece: \"{copied}\"")
    placeholders = re.findall(r"\[CHECK:[^\]]*\]", text)
    if placeholders:
        issues.append(f"placeholder: {len(placeholders)} [CHECK] detail(s) for Meera to fill")
    return issues


def _copied_phrase(text, n=12):
    tokens = re.findall(r"[a-z0-9%.']+", text.lower())
    grams = {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}
    for piece in published_pieces().values():
        pt = re.findall(r"[a-z0-9%.']+", piece.lower())
        for i in range(len(pt) - n + 1):
            g = " ".join(pt[i:i + n])
            if g in grams:
                return g
    return None


def autofix(text):
    """Only mechanical fixes that can't change meaning."""
    text = re.sub(r"\s*[—–]\s*", " - ", text)
    text = text.replace("**", "")
    return text.strip()


# --------------------------------------------------------------------------

def run_once(note_text):
    t = triage(note_text)
    print(json.dumps(t.model_dump(), indent=2))
    if t.verdict != "develop":
        return
    ref = find_reference(t.angle, t.search_queries)
    print("\nREFERENCE:", json.dumps(ref, indent=2))
    body, issues = draft_post(note_text, t.model_dump(), ref)
    print("\n" + "=" * 70 + "\n" + body + "\n" + "=" * 70)
    print("Word count:", len(body.split()), "| Issues:", issues or "none")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python pipeline.py "note text"')
    run_once(" ".join(sys.argv[1:]))
