"""LLM integration — GitHub Models API (OpenAI-compatible chat completions)."""

import json
import logging
import asyncio
from typing import Optional

import httpx

from . import config
from .schemas import Extraction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a metadata extraction engine. Given a user's raw journal entry, \
return ONLY a JSON object with these exact keys:

{
  "mood": "<single word or short phrase describing emotional tone>",
  "topics": ["<topic1>", "<topic2>", ...],
  "projects": ["<project1>", ...],
  "summary": "<one concise sentence summarizing the entry>"
}

Rules:
- Return ONLY valid JSON, no markdown fences, no explanation.
- If unsure about a field, use reasonable defaults (mood: "neutral", empty lists).
- Keep the summary under 20 words.
- Topics should be lowercase single words or short phrases.
"""

MAX_RETRIES = 3
RETRY_BACKOFF = 10  # seconds — GitHub Models has aggressive rate limits


async def extract_metadata(text: str) -> Extraction:
    """Send text to GitHub Models and parse structured metadata.

    Returns Extraction.fallback() if all retries fail — the entry
    still gets logged, just without rich metadata.
    """
    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "max_completion_tokens": 300,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    config.LLM_ENDPOINT,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

            # Log raw response for debugging
            content = data["choices"][0]["message"]["content"]
            logger.debug("LLM raw response: %s", content[:500] if content else "(empty)")

            # Handle empty responses
            if not content or not content.strip():
                raise ValueError("LLM returned empty content")

            content = content.strip()

            # Strip <think>...</think> blocks from reasoning models (e.g. Phi-4)
            if "<think>" in content:
                import re
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            # Strip markdown code fences if the model wraps the JSON
            if content.startswith("```"):
                content = content.split("\n", 1)[1]  # remove opening fence
                content = content.rsplit("```", 1)[0]  # remove closing fence
                content = content.strip()

            extraction = Extraction.model_validate_json(content)
            logger.info("LLM extraction succeeded (attempt %d): %s", attempt, extraction.summary)
            return extraction

        except httpx.HTTPStatusError as e:
            logger.warning(
                "LLM HTTP error (attempt %d/%d): %s %s",
                attempt, MAX_RETRIES, e.response.status_code, e.response.text[:200],
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "LLM parse error (attempt %d/%d): %s",
                attempt, MAX_RETRIES, e,
            )
        except httpx.TimeoutException:
            logger.warning("LLM timeout (attempt %d/%d)", attempt, MAX_RETRIES)
        except Exception as e:
            logger.error(
                "LLM unexpected error (attempt %d/%d): %s",
                attempt, MAX_RETRIES, e,
            )

        if attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF * attempt  # 10s, 20s
            logger.info("Retrying in %ds...", wait)
            await asyncio.sleep(wait)

    logger.error("All LLM retries exhausted — using fallback extraction")
    return Extraction.fallback()


# ── Verbatim formatting ──────────────────────────────────────────────────────

VERBATIM_SYSTEM_PROMPT = """\
You are a lossless information formatter. You receive raw, unstructured text \
(stream-of-consciousness notes, brain dumps, mixed topics) and your ONLY job \
is to reorganize and format it into clean, readable markdown.

## Absolute rules — violations are unacceptable
1. **NEVER remove, omit, compress, paraphrase, or summarize ANY information.** \
   Every single detail, number, name, date, thought, and aside in the input \
   MUST appear in your output. This is non-negotiable.
2. **NEVER add information** that is not in the original input. No commentary, \
   no suggestions, no "you might also consider" additions.
3. **Output raw markdown only.** No code fences wrapping the output, no \
   preamble, no explanation — just the formatted content.

## What you SHOULD do
- Group related items under descriptive markdown headers (##, ###)
- Use bullet points and sub-bullets to organize lists
- Bold key terms, names, dates, and deadlines for scannability
- Fix obvious typos and grammar only if it doesn't change meaning
- Separate distinct topics with clear section breaks
- Preserve the original tone and voice (casual stays casual)

## Example
Input: "BJJ today drilled x-guard sweep to SLX, also need groceries \
milk eggs bread. Meeting with Prof Chen about MRI project deadline March 15. \
Feeling anxious about finals. Should I learn Rust?"

Output:
## BJJ Training
- Drilled **x-guard sweep** to **SLX**

## Errands
- Groceries: **milk**, **eggs**, **bread**

## MRI Project
- Meeting with **Prof Chen**
- Deadline: **March 15**

## Personal
- Feeling anxious about **finals**
- Thought: should I learn **Rust**?
"""


async def format_verbatim(text: str) -> str:
    """Send text to LLM for lossless formatting/organization.

    The LLM restructures the text into clean markdown without removing
    any information. Falls back to returning the original text on failure.
    """
    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": VERBATIM_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "max_completion_tokens": 2000,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    config.LLM_ENDPOINT,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

            content = data["choices"][0]["message"]["content"]
            logger.debug("Verbatim LLM raw response: %s", content[:500] if content else "(empty)")

            if not content or not content.strip():
                raise ValueError("LLM returned empty content for verbatim formatting")

            content = content.strip()

            # Strip <think>...</think> blocks from reasoning models
            if "<think>" in content:
                import re
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            # Strip markdown code fences if the model wraps the output
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                content = content.rsplit("```", 1)[0]
                content = content.strip()

            logger.info("Verbatim formatting succeeded (attempt %d): %d chars", attempt, len(content))
            return content

        except httpx.HTTPStatusError as e:
            logger.warning(
                "Verbatim LLM HTTP error (attempt %d/%d): %s %s",
                attempt, MAX_RETRIES, e.response.status_code, e.response.text[:200],
            )
        except (KeyError, ValueError) as e:
            logger.warning(
                "Verbatim LLM parse error (attempt %d/%d): %s",
                attempt, MAX_RETRIES, e,
            )
        except httpx.TimeoutException:
            logger.warning("Verbatim LLM timeout (attempt %d/%d)", attempt, MAX_RETRIES)
        except Exception as e:
            logger.error(
                "Verbatim LLM unexpected error (attempt %d/%d): %s",
                attempt, MAX_RETRIES, e,
            )

        if attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF * attempt
            logger.info("Retrying in %ds...", wait)
            await asyncio.sleep(wait)

    logger.error("All verbatim LLM retries exhausted — returning raw text")
    return text