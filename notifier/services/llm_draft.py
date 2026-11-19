"""LLM-drafted message templates via Sarvam Token Factory (OpenAI-compatible).

The chat endpoint accepts the same SARVAM_API_KEY (Bearer auth). Drafting is
optional and failure-safe: a missing key or a failed call returns None and the
caller falls back to a sensible default template, so the dispatch pipeline is
never blocked. Only known placeholders are permitted, so the drafted template
flows through the existing personalize -> translate -> voice pipeline unchanged.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

CHAT_URL = "https://api.sarvam.ai/v2/chat/completions"

PROMPT = (
    "Write a short, warm voice reminder message for a small business to send a "
    "client. Event type: {event_type}. What it is about: {details}. Rules: at most "
    "2 sentences and 35 words, friendly and concrete, no emoji, no quotation "
    "marks, no greeting beyond the name. You may use only these placeholders: "
    "{{name}} {{details}} {{event_type_display}} {{time}} {{date}} {{time_left}}. "
    "Output ONLY the message template text, nothing else."
)


def _headers():
    key = os.getenv("SARVAM_API_KEY", "")
    if not key:
        return None
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def draft_message_template(event_type, details, model=None):
    """Return an LLM-drafted message_template string, or None on any failure.

    Never raises; the caller decides what to fall back to.
    """
    headers = _headers()
    if headers is None:
        logger.info("SARVAM_API_KEY not set - skipping LLM template drafting.")
        return None

    payload = {
        "model": model or os.getenv("SARVAM_LLM_MODEL", "sarvam-105b"),
        "messages": [
            {
                "role": "user",
                "content": PROMPT.format(
                    event_type=event_type, details=(details or "the event").strip() or "the event"
                ),
            }
        ],
        "temperature": 0.7,
        "max_tokens": 120,
    }
    try:
        response = requests.post(CHAT_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        choices = response.json().get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
    except (requests.RequestException, ValueError, TypeError, IndexError) as exc:
        logger.error("LLM template drafting failed: %s", exc)
        return None

    text = (content or "").strip().strip('`"').strip()
    if not text:
        logger.error("LLM returned no template text.")
        return None
    return text
