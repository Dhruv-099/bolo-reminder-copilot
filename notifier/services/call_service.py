"""Voice-call delivery via Sarvam Voice Agents (Samvaad) instant outbound API.

Places a single AI call per dispatched reminder: the agent speaks the already
translated reminder as its opening line (initial_bot_message override), then
follows its own instruction - which should ask the client to confirm and set
an output variable (client_reply by default) - and the outcome arrives via
webhook at /notifier/webhook/call/, where connected + a yes/no reply flows
into record_response like any other channel.

A separate workspace API key from Voice Agents settings is required
(SARVAM_VOICE_API_KEY) - the model-platform SARVAM_API_KEY is not used here.
Failure-safe: missing config or a failed call returns {"ok": False, ...}.
"""

import logging
import os

import requests

from notifier.utils import public_base_url

logger = logging.getLogger(__name__)

SARVAM_OUTBOUND_URL = (
    "https://apps.sarvam.ai/api/outbounds/v1/orgs/{org}/workspaces/{workspace}/outbounds"
)

# BCP-47 -> the language enum the instant-outbound API accepts.
LANGUAGE_NAMES = {
    "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi", "od-IN": "Odia",
    "pa-IN": "Punjabi", "ta-IN": "Tamil", "te-IN": "Telugu",
}


def _voice_configured():
    return all(
        os.getenv(name)
        for name in (
            "SARVAM_VOICE_API_KEY",
            "SARVAM_VOICE_ORG_ID",
            "SARVAM_VOICE_WORKSPACE_ID",
            "SARVAM_VOICE_APP_ID",
            "SARVAM_VOICE_CONNECTION_ID",
            "SARVAM_VOICE_NUMBER",
        )
    )


def _voice_app_version():
    try:
        return max(1, int(os.getenv("SARVAM_VOICE_APP_VERSION", "1")))
    except ValueError:
        return 1


def place_call(event, package):
    """Place one instant outbound AI call for a dispatched reminder."""
    if not _voice_configured():
        return {"ok": False, "error": "not_configured"}

    text_local = package.get("text_local") or package.get("text_english") or ""
    language = LANGUAGE_NAMES.get(event.person.preferred_language, "English")

    webhook_token = os.getenv("BOLO_WEBHOOK_TOKEN", "")
    webhook_config = None
    if webhook_token:
        # Sarvam's inline webhook_config carries no headers, so the token
        # rides in the query string; metadata correlates the outcome back
        # to this Event when the webhook fires.
        webhook_config = {
            "url": f"{public_base_url()}/notifier/webhook/call/?token={webhook_token}",
            "metadata": {"event_id": event.id},
        }

    payload = {
        "app_config": {
            "app_id": os.getenv("SARVAM_VOICE_APP_ID"),
            "app_version": _voice_app_version(),
            "connection_config": {
                "connection_id": os.getenv("SARVAM_VOICE_CONNECTION_ID"),
                "agent_phone_number": os.getenv("SARVAM_VOICE_NUMBER"),
            },
            # The agent opens by speaking our translated reminder, in the
            # person's own language - then follows its own instruction to
            # collect the confirmation.
            "app_overrides": {
                "initial_bot_message": text_local,
                "initial_language_name": language,
            },
            "agent_variables": {
                "person_name": event.person.name,
                "reminder_text": text_local,
                "respond_url": f"{public_base_url()}/notifier/respond/{event.id}/",
            },
        },
        "user_config": {"user_phone_number": event.person.phone_number},
    }
    if webhook_config:
        payload["webhook_config"] = webhook_config

    url = SARVAM_OUTBOUND_URL.format(
        org=os.getenv("SARVAM_VOICE_ORG_ID"),
        workspace=os.getenv("SARVAM_VOICE_WORKSPACE_ID"),
    )
    headers = {
        "X-API-Key": os.getenv("SARVAM_VOICE_API_KEY"),
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Sarvam outbound call failed for event %s: %s", event.id, exc)
        return {"ok": False, "error": "call_failed", "detail": str(exc)}

    attempt_id = data.get("attempt_id")
    if not attempt_id:
        logger.error("Sarvam outbound returned no attempt_id for event %s: %s", event.id, data)
        return {"ok": False, "error": "no_attempt_id", "detail": data}
    logger.info("Voice call placed for event %s: attempt %s", event.id, attempt_id)
    return {"ok": True, "attempt_id": attempt_id, "language": language}
