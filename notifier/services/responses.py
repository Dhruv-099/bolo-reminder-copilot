"""Client-side reply capture for dispatched voice reminders.

The press-1 / press-2 loop. `record_response` is the single entry point the
demo page uses; a production IVR (DTMF webhook) or a WhatsApp reply handler
posts the same digit into it, so nothing else changes when the delivery leg
goes live. Re-pressing simply updates the reply (latest wins).
"""

import logging

from django.utils import timezone

from notifier.models import Event

logger = logging.getLogger(__name__)

DIGIT_MAP = {
    "1": Event.ResponseValue.CONFIRMED,
    "2": Event.ResponseValue.DECLINED,
}


def record_response(event_id, digit, channel="demo_page"):
    """Store a client's press-1/press-2 reply for a dispatched event.

    Returns a report dict with ok/message; never raises.
    """
    digit = str(digit or "").strip()
    if digit not in DIGIT_MAP:
        return {
            "ok": False,
            "error": "invalid_digit",
            "message": "Please reply with 1 (yes) or 2 (no).",
        }
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return {"ok": False, "error": "event_not_found", "message": "Unknown reminder."}

    if not event.voice_dispatched_at:
        return {
            "ok": False,
            "error": "not_dispatched",
            "message": "This reminder has not been sent yet, so there is nothing to confirm.",
        }

    event.response_value = DIGIT_MAP[digit]
    event.response_received_at = timezone.now()
    event.response_channel = channel
    event.save(update_fields=["response_value", "response_received_at", "response_channel"])

    logger.info(
        "Recorded reply %s for event %s via %s.",
        event.response_value,
        event.id,
        channel,
    )
    return {
        "ok": True,
        "event_id": event.id,
        "value": event.response_value,
        "message": f"Reply recorded: {event.get_response_value_display()}.",
    }
