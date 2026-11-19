"""Composable reminder-dispatch workflow for the Bolo copilot.

These are the functions Synapse Custom mode composes into one MCP tool,
`dispatch_due_reminders`. The module is standalone-safe: importing it
bootstraps Django, so it works from the management command, a plain script,
or the Synapse-generated MCP server without a running web server.
"""

import logging
import os
import re

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notification_project.settings")

import django
from django.apps import apps as django_apps

if not django_apps.ready:
    django.setup()

from django.db import transaction
from django.utils import timezone

from notifier.models import Event
from notifier.services import sarvam_service
from notifier.utils import format_time_left

logger = logging.getLogger(__name__)

FALLBACK_LANGUAGE = "en-IN"

# Channel-layer group that receives live progress events (see _emit_progress).
PROGRESS_GROUP = "bolo.dispatch"


def _emit_progress(payload):
    """Push a live progress event to any connected dashboard.

    Import-guarded: without Channels installed — or with no ASGI server
    running, e.g. from the management command, cron or the MCP server —
    this is a no-op, so the workflow behaves identically everywhere.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(
                PROGRESS_GROUP, {"type": "dispatch.progress", "payload": payload}
            )
    except Exception:  # noqa: BLE001 - progress must never break a dispatch
        logger.debug("Live progress event dropped (no channel layer).", exc_info=True)


def find_due_events():
    """Return reminder events that are due, not dispatched and not cancelled, soonest first."""
    pending = Event.objects.filter(
        voice_dispatched_at__isnull=True, cancelled_at__isnull=True
    ).order_by("event_datetime")
    return [event for event in pending if event.is_notification_due]


def retry_after_minutes():
    """Minutes to wait for a reply before re-attempting a dispatched reminder."""
    try:
        return max(0, int(os.getenv("BOLO_RETRY_AFTER_MINUTES", "360")))
    except ValueError:
        return 360


def max_dispatch_attempts():
    """Upper bound on dispatch attempts per reminder (the retry ladder)."""
    try:
        return max(1, int(os.getenv("BOLO_MAX_DISPATCH_ATTEMPTS", "2")))
    except ValueError:
        return 2


def find_retry_due_events():
    """Dispatched reminders that got no reply and are due for another attempt."""
    cutoff = timezone.now() - timezone.timedelta(minutes=retry_after_minutes())
    return list(
        Event.objects.filter(
            voice_dispatched_at__isnull=False,
            voice_dispatched_at__lte=cutoff,
            response_value=Event.ResponseValue.PENDING,
            dispatch_attempts__lt=max_dispatch_attempts(),
            cancelled_at__isnull=True,
        ).order_by("event_datetime")
    )


def is_quiet_now(person, at=None):
    """True inside the person's quiet window; supports windows that wrap midnight."""
    if not (person.quiet_hours_start and person.quiet_hours_end):
        return False
    at = (at or timezone.localtime()).time()
    start, end = person.quiet_hours_start, person.quiet_hours_end
    if start <= end:
        return start <= at < end
    return at >= start or at < end


def personalize_message(event):
    """Render an Event's message_template with the person's first name, the
    event details, date and time.

    Never raises on odd templates: an unknown placeholder is replaced with a
    safe default and any leftover braces are dropped.
    """
    person_name = event.person.name or "there"
    context = {
        "name": person_name.split()[0],
        "event_type_display": event.get_event_type_display().lower(),
        "details": event.details or "",
        "time": event.event_datetime.strftime("%I:%M %p"),
        "date": event.event_datetime.strftime("%B %d, %Y"),
        "time_left": format_time_left(event.event_datetime),
    }
    try:
        return event.message_template.format(**context).strip()
    except (KeyError, IndexError) as exc:
        logger.warning(
            "Event %s template has an unknown placeholder (%s); rendering safely.",
            event.id,
            exc,
        )
        text = event.message_template
        for key, value in context.items():
            text = text.replace("{" + key + "}", str(value))
        return re.sub(r"\{[^{}]*\}", "", text).strip()


def prepare_reminder(event):
    """Personalize, translate and voice one due event. No database changes.

    Returns a report dict with the message in English and in the person's
    preferred language, plus the generated audio path.
    """
    language = event.person.preferred_language
    if language not in sarvam_service.SUPPORTED_LANGUAGES:
        language = FALLBACK_LANGUAGE

    text_english = personalize_message(event)
    report = {
        "event_id": event.id,
        "person": event.person.name,
        "event_type": event.get_event_type_display(),
        "event_datetime": timezone.localtime(event.event_datetime).isoformat(),
        "language": language,
        "text_english": text_english,
        "text_local": None,
        "audio_path": None,
        "audio_file": None,
        "status": "drafted",
        "error": None,
    }

    if language == FALLBACK_LANGUAGE:
        report["text_local"] = text_english
    else:
        translated = sarvam_service.translate_message(text_english, language)
        if translated is None:
            report["status"] = "error"
            report["error"] = "translation_failed"
            return report
        report["text_local"] = translated

    audio_path = sarvam_service.generate_voice_audio(
        report["text_local"], language, event_id=event.id
    )
    if audio_path is None:
        report["status"] = "error"
        report["error"] = "audio_failed"
        return report

    report["audio_path"] = audio_path
    report["audio_file"] = sarvam_service.audio_absolute_path(audio_path)
    report["status"] = "ready"
    return report


def dispatch_due_reminders(confirm=False, event_ids=None):
    """The composed workflow: find due events, personalize, translate, voice.

    Preview mode (confirm=False) drafts every due reminder with its audio and
    changes nothing. Execute mode (confirm=True) additionally records the
    dispatch timestamp and audio path — and it is idempotent: already
    dispatched events are never re-sent. Pass event_ids to scope the run.
    """
    due_events = [(event, False) for event in find_due_events()]
    due_events += [(event, True) for event in find_retry_due_events()]
    if event_ids:
        try:
            wanted = {int(event_id) for event_id in event_ids}
        except (TypeError, ValueError):
            wanted = set()
        due_events = [pair for pair in due_events if pair[0].id in wanted]

    _emit_progress({"phase": "start", "confirm": bool(confirm), "due": len(due_events)})

    results = []
    for event, is_retry in due_events:
        if is_quiet_now(event.person):
            # Respect quiet hours: draft nothing, send nothing. The event
            # simply stays due until its person's window opens.
            results.append(
                {
                    "event_id": event.id,
                    "person": event.person.name,
                    "event_type": event.get_event_type_display(),
                    "event_datetime": timezone.localtime(event.event_datetime).isoformat(),
                    "language": event.person.preferred_language,
                    "text_english": "",
                    "text_local": None,
                    "audio_path": None,
                    "audio_file": None,
                    "status": "held_quiet_hours",
                    "error": None,
                    "retry": is_retry,
                }
            )
            _emit_progress(
                {
                    "phase": "event",
                    "event_id": event.id,
                    "person": event.person.name,
                    "language": event.person.preferred_language,
                    "status": "held_quiet_hours",
                }
            )
            continue

        report = prepare_reminder(event)
        report["retry"] = is_retry
        if confirm and report["status"] == "ready":
            # Re-check under a row lock so two concurrent runs can never
            # double-send the same event. We compare against the in-memory
            # snapshot taken when the run was built: for fresh events a new
            # voice_dispatched_at means another run won the race; for retry
            # events the timestamp is legitimately set, so a changed attempt
            # count is the race signal. The lock is held only for the quick
            # write, not for the slow translate/voice calls above.
            # (SQLite ignores the row lock but serializes writes itself,
            # so the re-check still holds; Postgres gets true locking.)
            with transaction.atomic():
                locked = Event.objects.select_for_update().get(pk=event.pk)
                if (
                    locked.voice_dispatched_at != event.voice_dispatched_at
                    or locked.dispatch_attempts != event.dispatch_attempts
                ):
                    report["status"] = "skipped"
                    report["error"] = "already_dispatched_concurrently"
                else:
                    locked.voice_dispatched_at = timezone.now()
                    locked.voice_audio_path = report["audio_path"]
                    locked.voice_transcript_english = report["text_english"]
                    locked.voice_transcript_local = report["text_local"]
                    locked.dispatch_attempts += 1
                    locked.save(
                        update_fields=[
                            "voice_dispatched_at",
                            "voice_audio_path",
                            "voice_transcript_english",
                            "voice_transcript_local",
                            "dispatch_attempts",
                        ]
                    )
                    report["status"] = "dispatched"
        results.append(report)
        _emit_progress(
            {
                "phase": "event",
                "event_id": event.id,
                "person": event.person.name,
                "language": event.person.preferred_language,
                "status": report["status"],
            }
        )

    dispatched = sum(1 for item in results if item["status"] == "dispatched")
    failed = sum(1 for item in results if item["status"] == "error")
    skipped = sum(1 for item in results if item["status"] == "skipped")
    held = sum(1 for item in results if item["status"] == "held_quiet_hours")

    if not results:
        message = "No due events found."
    elif confirm:
        message = f"Dispatch complete: {dispatched} sent, {failed} failed."
        if skipped:
            message += f" {skipped} skipped (already dispatched by another run)."
        if held:
            message += f" {held} held back (quiet hours)."
    else:
        message = f"Preview: {len(results)} due, {failed} failed."
        if held:
            message += f" {held} held back (quiet hours)."
        message += " Nothing was sent — call again with confirm=true to dispatch."

    _emit_progress(
        {
            "phase": "done",
            "summary": message,
            "confirmed": bool(confirm),
            "dispatched": dispatched,
            "failed": failed,
            "skipped": skipped,
            "held": held,
        }
    )

    return {
        "confirmed": bool(confirm),
        "due_found": len(results),
        "dispatched": dispatched,
        "failed": failed,
        "skipped": skipped,
        "held_quiet_hours": held,
        "results": results,
        "message": message,
    }


def render_report(report):
    """Plain-text, line-per-event summary of a dispatch_due_reminders report."""
    lines = [
        f"Reminder dispatch - confirm={report.get('confirmed')}, "
        f"due={report.get('due_found', 0)}, dispatched={report.get('dispatched', 0)}, "
        f"failed={report.get('failed', 0)}, skipped={report.get('skipped', 0)}, "
        f"held={report.get('held_quiet_hours', 0)}"
    ]
    for item in report.get("results", []):
        retry_note = " (retry)" if item.get("retry") else ""
        if item.get("status") == "held_quiet_hours":
            lines.append(
                f"[held{retry_note}] #{item['event_id']} {item['person']} - {item['event_type']} "
                f"({item['language']}): inside quiet hours, not sent"
            )
            continue
        lines.append(
            f"[{item['status']}{retry_note}] #{item['event_id']} {item['person']} - {item['event_type']} "
            f"({item['language']}): {item.get('text_local') or item.get('text_english')}"
        )
        if item.get("audio_file"):
            lines.append(f"    audio: {item['audio_file']}")
        if item.get("error"):
            lines.append(
                f"    error: {item['error']} (Sarvam unreachable or key missing - see logs)"
            )
    return "\n".join(lines)
