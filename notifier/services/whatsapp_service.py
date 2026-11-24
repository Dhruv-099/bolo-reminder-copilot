"""WhatsApp delivery via the Meta WhatsApp Cloud API.

Sends the already-translated reminder as one interactive message: the
vernacular text plus the respond link, with Confirm / Decline quick-reply
buttons whose ids carry the event id (bolo:<event_id>:1|2), so the reply
webhook at /notifier/webhook/whatsapp/ correlates the answer back to this
Event the same way the Sarvam call metadata does. A plain-text yes/no reply
correlates by sender phone instead (newest dispatched, still-pending event).

Business-initiated caveat (the WhatsApp analogue of MSG91's DLT note):
freeform interactive messages deliver inside the 24-hour customer-service
window and to test numbers; a production campaign should use a pre-approved
utility template with quick-reply buttons, same payload shape.

Failure-safe: missing credentials or a failed call returns
{"ok": False, ...} and never raises.
"""

import logging
import os

import requests

from notifier.utils import respond_url

logger = logging.getLogger(__name__)

GRAPH_MESSAGES_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"

BUTTON_CONFIRM = "bolo:{event_id}:1"
BUTTON_DECLINE = "bolo:{event_id}:2"


def _whatsapp_configured():
    return bool(os.getenv("WHATSAPP_PHONE_NUMBER_ID") and os.getenv("WHATSAPP_ACCESS_TOKEN"))


def send_reminder_message(event, package):
    """Message the reminder in the person's language, with reply buttons.

    The body carries the vernacular reminder text plus the respond link
    (audio + press-1 / press-2), so the loop closes in-chat when the buttons
    render and over the link when they do not.
    """
    if not _whatsapp_configured():
        return {"ok": False, "error": "not_configured"}

    text = package.get("text_local") or package.get("text_english") or ""
    link = respond_url(event.id)
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        # Meta wants E.164 without the leading plus (like MSG91).
        "to": event.person.phone_number.lstrip("+"),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": f"{text}\n{link}"},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_CONFIRM.format(event_id=event.id),
                            "title": "Confirm",
                        },
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_DECLINE.format(event_id=event.id),
                            "title": "Decline",
                        },
                    },
                ]
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN')}",
        "Content-Type": "application/json",
    }
    url = GRAPH_MESSAGES_URL.format(phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID"))
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("WhatsApp send failed for event %s: %s", event.id, exc)
        return {"ok": False, "error": "send_failed", "detail": str(exc)}

    messages = data.get("messages") or []
    message_id = messages[0].get("id") if messages else None
    if not message_id:
        logger.error("WhatsApp rejected event %s: %s", event.id, data)
        return {"ok": False, "error": "send_rejected", "detail": data}
    logger.info("WhatsApp message sent for event %s: %s", event.id, message_id)
    return {
        "ok": True,
        "provider": "whatsapp_cloud",
        "message_id": message_id,
        "link": link,
    }
