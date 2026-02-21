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