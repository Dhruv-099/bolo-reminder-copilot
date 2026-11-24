"""The delivery leg: route each dispatched package by the person's channel
preference.

Every dispatched reminder passes through deliver() after the confirm-gated
write. Real channel senders (WhatsApp Cloud API, Sarvam Voice Agents, MSG91)
plug in as handlers below; when their credentials are absent each channel
falls back to the recorded demo package, so the dispatch workflow - preview,
confirm, retry ladder, responses - behaves identically either way.

Replies come back through the token-gated webhooks (response/ for bridge
digits, call/ for Sarvam outcomes, whatsapp/ for Meta callbacks - see
views); the demo respond page posts the same digits into record_response.
"""

import logging

from notifier.models import Person
from notifier.services import call_service, sms_service, whatsapp_service

logger = logging.getLogger(__name__)


def deliver_via_demo_page(event, package):
    """The prototype's native channel: the recorded package + client page."""
    return {"via": "demo_page", "mode": "recorded"}


def deliver_via_whatsapp(event, package):
    """Message the reminder with Confirm / Decline buttons via the Meta
    WhatsApp Cloud API when configured."""
    result = whatsapp_service.send_reminder_message(event, package)
    if result["ok"]:
        return {
            "via": "whatsapp",
            "mode": "sent:whatsapp_cloud",
            "message_id": result.get("message_id"),
            "link": result.get("link"),
        }
    return {
        "via": "whatsapp",
        "mode": "recorded_fallback:whatsapp",
        "note": f"whatsapp not sent ({result.get('error')}) - package recorded",
    }


def deliver_via_sms(event, package):
    """Text the reminder + respond link through MSG91 when configured."""
    result = sms_service.send_reminder_sms(event, package)
    if result["ok"]:
        return {"via": "sms", "mode": "sent:msg91", "link": result.get("link")}
    return {
        "via": "sms",
        "mode": "recorded_fallback:sms",
        "note": f"sms not sent ({result.get('error')}) - package recorded",
    }


def deliver_via_call(event, package):
    """Place a Sarvam Voice Agents AI call when configured."""
    result = call_service.place_call(event, package)
    if result["ok"]:
        return {
            "via": "voice_call",
            "mode": "call_placed",
            "attempt_id": result.get("attempt_id"),
            "language": result.get("language"),
        }
    return {
        "via": "voice_call",
        "mode": "recorded_fallback:voice_call",
        "note": f"call not placed ({result.get('error')}) - package recorded",
    }


CHANNEL_HANDLERS = {
    Person.ChannelPreference.WHATSAPP: deliver_via_whatsapp,
    Person.ChannelPreference.VOICE_CALL: deliver_via_call,
    Person.ChannelPreference.SMS: deliver_via_sms,
    Person.ChannelPreference.DEMO_PAGE: deliver_via_demo_page,
}


def deliver(event, package):
    """Route a dispatched package by the person's channel preference.

    Never raises: a handler failure becomes a delivery report entry so one
    bad channel never breaks the dispatch batch.
    """
    channel = event.person.channel_preference
    handler = CHANNEL_HANDLERS.get(channel)
    if handler is None:
        handler = deliver_via_demo_page
    try:
        return handler(event, package)
    except Exception as exc:  # noqa: BLE001 - delivery must never break dispatch
        logger.error("Delivery handler failed for event %s: %s", event.id, exc)
        return {"via": channel, "mode": "error", "error": str(exc)}
