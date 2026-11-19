"""Agent-facing reminder tools for the Bolo MCP server.

Each function returns a plain report dict and never raises, so the tools stay
safe to call conversationally. Everything here operates on the same Django
data the dashboard and admin use — the agent gets no separate source of truth.

Tools:
- add_reminder(person, what, when)        — conversational intake
- reschedule_event(event_id, when)        — move one, reset sends if needed
- cancel_reminder(event_id)               — cancelled events are never sent
- list_responses([status])                 — who confirmed / declined / is silent
- reminder_digest()                        — morning round: due, replies, retries
"""

import logging
import os
from datetime import datetime

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notification_project.settings")

import django
from django.apps import apps as django_apps

if not django_apps.ready:
    django.setup()

from django.utils import timezone

from notifier.models import LANGUAGES, Event, Person
from notifier.services import llm_draft
from notifier.services.reminder_dispatch import find_retry_due_events

logger = logging.getLogger(__name__)

LANGUAGE_CODES = [code for code, _name in LANGUAGES]

DEFAULT_TEMPLATE = (
    "Hi {name}, a friendly reminder: your {event_type_display} '{details}' is "
    "{time_left}, at {time} on {date}."
)


def _parse_when(when):
    """Parse 'when' into an aware datetime; naive input uses local time.

    Accepts 'YYYY-MM-DD HH:MM[:SS]', 'YYYY-MM-DDTHH:MM[:SS]' (fromisoformat)
    or a plain date 'YYYY-MM-DD', which becomes 09:00 local. Returns
    (datetime, None) on success or (None, error_message).
    """
    if isinstance(when, datetime):
        parsed = when
    else:
        text = str(when or "").strip()
        if not text:
            return None, "Give a date and time, e.g. '2026-11-22 16:00'."
        try:
            parsed = datetime.fromisoformat(text.replace("T", " "))
        except ValueError:
            return None, f"Could not read '{text}' as a date/time. Try '2026-11-22 16:00'."
        # Date-only input means "that morning" by default.
        if len(text) <= 10 and parsed.hour == 0 and parsed.minute == 0:
            parsed = parsed.replace(hour=9)

    if parsed.tzinfo is None:
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed, None


def _get_event(event_id):
    try:
        return Event.objects.select_related("person").get(pk=event_id), None
    except (Event.DoesNotExist, ValueError, TypeError):
        return None, {"ok": False, "error": "event_not_found", "message": f"No event #{event_id}."}


def _parse_lead_minutes(lead_minutes, default=120):
    try:
        return max(0, int(lead_minutes))
    except (TypeError, ValueError):
        return default


def add_reminder(
    person_name,
    phone_number,
    what,
    when,
    preferred_language="en-IN",
    event_type="REMINDER",
    lead_minutes=120,
    details="",
    message_template=None,
    person_email=None,
):
    """Create (or reuse) a Person by phone number and schedule a reminder Event.

    If no message_template is given, one is drafted by the LLM (or the built-in
    default when the LLM is unavailable). Nothing is sent here — dispatch
    happens when the reminder's lead time arrives, via dispatch_due_reminders.
    """
    name = (person_name or "").strip()
    what = (what or "").strip()
    phone = Person.normalize_phone(str(phone_number or ""))

    if not name:
        return {"ok": False, "error": "missing_person_name", "message": "Who should I remind? I need their name."}
    if not what:
        return {"ok": False, "error": "missing_what", "message": "What should I remind them about?"}
    if not phone.startswith("+"):
        return {
            "ok": False,
            "error": "invalid_phone",
            "message": "Give a phone number in international format, e.g. +919876543210.",
        }

    when_dt, parse_error = _parse_when(when)
    if when_dt is None:
        return {"ok": False, "error": "invalid_when", "message": parse_error}

    language = preferred_language if preferred_language in LANGUAGE_CODES else "en-IN"
    try:
        type_code = Event.EventType(str(event_type or "").strip().upper()).value
    except ValueError:
        return {
            "ok": False,
            "error": "invalid_event_type",
            "message": "event_type must be one of: " + ", ".join(Event.EventType.values),
        }

    person, created = Person.objects.get_or_create(
        phone_number=phone,
        defaults={"name": name, "preferred_language": language, "email": person_email or None},
    )
    if not created:
        changed = []
        if person.name != name:
            person.name = name
            changed.append("name")
        if person.preferred_language != language:
            person.preferred_language = language
            changed.append("preferred_language")
        if person_email and person.email != person_email:
            person.email = person_email
            changed.append("email")
        if changed:
            person.save(update_fields=changed)

    if message_template:
        template = message_template
    else:
        template = llm_draft.draft_message_template(event_type=type_code, details=details or what)
        if not template:
            template = DEFAULT_TEMPLATE

    event = Event.objects.create(
        person=person,
        event_type=type_code,
        event_datetime=when_dt,
        message_template=template,
        details=details or what,
        notification_lead_time_minutes=_parse_lead_minutes(lead_minutes),
    )
    trigger_at = event.event_datetime - timezone.timedelta(minutes=event.notification_lead_time_minutes)
    return {
        "ok": True,
        "event_id": event.id,
        "person": person.name,
        "phone_number": person.phone_number,
        "person_created": created,
        "preferred_language": person.preferred_language,
        "event_type": event.get_event_type_display(),
        "when_local": timezone.localtime(event.event_datetime).isoformat(),
        "reminder_fires_at": timezone.localtime(trigger_at).isoformat(),
        "message_template": template,
        "template_drafted_by_llm": not bool(message_template),
        "message": (
            f"Reminder #{event.id} scheduled: {person.name} "
            f"({person.get_preferred_language_display()}) gets a voice reminder about "
            f"'{details or what}' — dispatched from {timezone.localtime(trigger_at):%Y-%m-%d %H:%M} local."
        ),
    }


def reschedule_event(event_id, when, note=""):
    """Move an event to a new time.

    If the voice reminder was already dispatched, its send state is reset so
    the reminder goes out again for the new time (the client's old reply is
    cleared too — a fresh slot needs a fresh confirmation).
    """
    event, error = _get_event(event_id)
    if error:
        return error

    when_dt, parse_error = _parse_when(when)
    if when_dt is None:
        return {"ok": False, "error": "invalid_when", "message": parse_error}
    if event.cancelled_at:
        return {
            "ok": False,
            "error": "cancelled",
            "message": f"#{event.id} was cancelled — use add_reminder to schedule a fresh one.",
        }

    reset_dispatch = bool(event.voice_dispatched_at)
    old_when = event.event_datetime
    event.event_datetime = when_dt
    if reset_dispatch:
        event.voice_dispatched_at = None
        event.voice_audio_path = ""
        event.voice_transcript_english = ""
        event.voice_transcript_local = ""
        event.response_value = Event.ResponseValue.PENDING
        event.response_received_at = None
        event.response_channel = ""
        event.dispatch_attempts = 0
        event.save()
    else:
        event.save(update_fields=["event_datetime"])

    trigger_at = when_dt - timezone.timedelta(minutes=event.notification_lead_time_minutes)
    return {
        "ok": True,
        "event_id": event.id,
        "person": event.person.name,
        "old_when_local": timezone.localtime(old_when).isoformat(),
        "new_when_local": timezone.localtime(when_dt).isoformat(),
        "send_state_reset": reset_dispatch,
        "reminder_fires_at": timezone.localtime(trigger_at).isoformat(),
        "message": (
            f"Moved #{event.id} for {event.person.name} to "
            f"{timezone.localtime(when_dt):%Y-%m-%d %H:%M}."
            + (" The old voice reminder and reply were cleared; it will be re-sent for the new time." if reset_dispatch else "")
            + (f" Note: {note}" if note else "")
        ),
    }


def cancel_reminder(event_id, reason=""):
    """Cancel a reminder. Cancelled events are never sent."""
    event, error = _get_event(event_id)
    if error:
        return error
    if event.cancelled_at:
        return {"ok": False, "error": "already_cancelled", "message": f"#{event.id} is already cancelled."}

    event.cancelled_at = timezone.now()
    event.save(update_fields=["cancelled_at"])
    if reason:
        logger.info("Event %s cancelled: %s", event.id, reason)
    return {
        "ok": True,
        "event_id": event.id,
        "person": event.person.name,
        "event_type": event.get_event_type_display(),
        "cancelled_at": timezone.localtime().isoformat(),
        "message": (
            f"Cancelled reminder #{event.id} for {event.person.name}."
            + (f" Reason: {reason}" if reason else "")
        ),
    }


def list_responses(status=None):
    """The confirmation follow-up view: who confirmed, who declined, who is silent.

    Optional status filter: 'confirmed', 'declined' or 'pending'.
    """
    qs = Event.objects.filter(
        voice_dispatched_at__isnull=False, cancelled_at__isnull=True
    ).select_related("person")
    wanted = str(status or "").strip().upper()
    if wanted in {"CONFIRMED", "DECLINED", "PENDING"}:
        qs = qs.filter(response_value=wanted)

    rows = []
    for event in qs.order_by("-voice_dispatched_at"):
        rows.append(
            {
                "event_id": event.id,
                "person": event.person.name,
                "phone_number": event.person.phone_number,
                "language": event.person.preferred_language,
                "event_type": event.get_event_type_display(),
                "dispatched_at": timezone.localtime(event.voice_dispatched_at).isoformat(),
                "response": event.response_value,
                "response_display": event.get_response_value_display(),
                "response_received_at": (
                    timezone.localtime(event.response_received_at).isoformat()
                    if event.response_received_at
                    else None
                ),
                "response_channel": event.response_channel or None,
                "attempts": event.dispatch_attempts,
            }
        )

    confirmed = sum(1 for row in rows if row["response"] == Event.ResponseValue.CONFIRMED)
    declined = sum(1 for row in rows if row["response"] == Event.ResponseValue.DECLINED)
    pending = sum(1 for row in rows if row["response"] == Event.ResponseValue.PENDING)
    return {
        "ok": True,
        "filter": wanted or None,
        "dispatched": len(rows),
        "confirmed": confirmed,
        "declined": declined,
        "awaiting_reply": pending,
        "results": rows,
        "message": (
            f"{len(rows)} dispatched: {confirmed} confirmed, {declined} declined, "
            f"{pending} awaiting reply."
        ),
    }


def reminder_digest():
    """One-call morning-round summary: due today, replies awaited, retries eligible."""
    now = timezone.now()
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    due_today = [
        {
            "event_id": event.id,
            "person": event.person.name,
            "language": event.person.preferred_language,
            "event_type": event.get_event_type_display(),
            "when_local": timezone.localtime(event.event_datetime).isoformat(),
        }
        for event in Event.objects.filter(
            voice_dispatched_at__isnull=True,
            cancelled_at__isnull=True,
            event_datetime__lte=today_end,
        )
        .select_related("person")
        .order_by("event_datetime")
        if event.is_notification_due
    ]

    responses = list_responses()
    retries = [
        {
            "event_id": event.id,
            "person": event.person.name,
            "language": event.person.preferred_language,
            "attempts": event.dispatch_attempts,
        }
        for event in find_retry_due_events()
    ]

    return {
        "ok": True,
        "generated_at": timezone.localtime(now).isoformat(),
        "due_today": due_today,
        "awaiting_reply": responses["awaiting_reply"],
        "confirmed": responses["confirmed"],
        "declined": responses["declined"],
        "retry_due": retries,
        "message": (
            f"Today: {len(due_today)} reminder(s) due, "
            f"{responses['awaiting_reply']} still awaiting a reply, "
            f"{len(retries)} eligible for a retry nudge."
        ),
    }
