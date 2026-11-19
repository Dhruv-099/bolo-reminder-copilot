"""The delivery leg: route each dispatched package by the person's channel
preference.

Every dispatched reminder passes through deliver() after the confirm-gated
write. Real channel senders (WhatsApp Cloud API / Twilio, an IVR telephony
bridge, an SMS gateway) plug in as handlers below; until their wiring lands,
each channel falls back to the recorded demo package, so the dispatch
workflow - preview, confirm, retry ladder, responses - behaves identically
either way.

Replies from real channels come back through /notifier/webhook/response/
(sees views.response_webhook); the demo respond page posts the same digits.
"""

import logging
import os

from notifier.models import Person

logger = logging.getLogger(__name__)


def _whatsapp_configured():
    """True when Meta Cloud API (or Twilio sandbox) credentials are present."""
    if os.getenv("WHATSAPP_PHONE_NUMBER_ID") and os.getenv("WHATSAPP_ACCESS_TOKEN"):
        return True
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_WHATSAPP_FROM")
    )


def deliver_via_demo_page(event, package):
    """The prototype's native channel: the recorded package + client page."""
    return {"via": "demo_page", "mode": "recorded"}


def deliver_via_whatsapp(event, package):
    if _whatsapp_configured():
        # Credentials detected: the real sender lands here when the delivery
        # leg is wired (prototype-deferred). Record the package meanwhile.
        logger.info(
            "WhatsApp credentials detected; real sender lands here "
            "(prototype-deferred). Package recorded for event %s.",
            event.id,
        )
        return {"via": "whatsapp", "mode": "recorded_pending_real_send"}
    return {
        "via": "whatsapp",
        "mode": "recorded_fallback:whatsapp",
        "note": "no WhatsApp credentials in the environment - package recorded",
    }


def _deferred_handler(channel):
    """Handler for channels whose real sender is still to be wired."""

    def handler(event, package):
        return {
            "via": channel,
            "mode": f"recorded_fallback:{channel}",
            "note": "real sender lands here (prototype-deferred)",
        }

    return handler


CHANNEL_HANDLERS = {
    Person.ChannelPreference.WHATSAPP: deliver_via_whatsapp,
    Person.ChannelPreference.VOICE_CALL: _deferred_handler("voice_call"),
    Person.ChannelPreference.SMS: _deferred_handler("sms"),
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
