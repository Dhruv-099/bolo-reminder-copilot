# notifier/views.py
import hmac
import json
import os

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from notifier.models import LANGUAGES, Event, Person
from notifier.services.reminder_dispatch import dispatch_due_reminders, prepare_reminder
from notifier.services.responses import record_response


def home_page_view(request):
    context = {
        "project_name": "Bolo — Multilingual Voice Reminder Copilot",
        "description": "Event reminders drafted by an agent, spoken in each person's own language.",
    }
    return render(request, "notifier/home.html", context)


def dashboard(request):
    """The dispatch table: every reminder, what was said, and who replied."""
    events = Event.objects.select_related("person").order_by("event_datetime")
    rows = []
    stats = {
        "due": 0, "sent": 0, "scheduled": 0, "awaiting": 0,
        "confirmed": 0, "declined": 0, "cancelled": 0,
    }
    for event in events:
        if event.cancelled_at:
            status = "cancelled"
            stats["cancelled"] += 1
        elif event.voice_dispatched_at:
            status = "sent"
            stats["sent"] += 1
            if event.response_value == Event.ResponseValue.CONFIRMED:
                stats["confirmed"] += 1
            elif event.response_value == Event.ResponseValue.DECLINED:
                stats["declined"] += 1
            else:
                stats["awaiting"] += 1
        elif event.is_notification_due:
            status = "due"
            stats["due"] += 1
        else:
            status = "scheduled"
            stats["scheduled"] += 1
        rows.append({"event": event, "status": status})

    return render(request, "notifier/dashboard.html", {"rows": rows, "stats": stats})


@require_POST
def event_preview(request, event_id):
    """Draft one event's reminder (translate + voice) without sending it."""
    event = get_object_or_404(Event, pk=event_id)
    if event.voice_dispatched_at:
        messages.info(request, f"#{event.id} was already sent — see the dashboard.")
        return redirect("notifier:dashboard")

    report = prepare_reminder(event)
    return render(request, "notifier/event_preview.html", {"event": event, "report": report})


@require_POST
def event_dispatch(request, event_id):
    """Dispatch a single event's voice reminder (the confirm-gated workflow)."""
    event = get_object_or_404(Event, pk=event_id)
    report = dispatch_due_reminders(confirm=True, event_ids=[event.id])
    item = (report.get("results") or [{}])[0]

    if item.get("status") == "dispatched":
        messages.success(
            request,
            f"Voice reminder sent to {event.person.name} "
            f"({event.person.preferred_language}).",
        )
    elif not report.get("results"):
        messages.info(
            request,
            f"#{event.id} was not sent — already dispatched, cancelled, held by quiet hours, or not due yet.",
        )
    elif item.get("status") == "error":
        messages.error(
            request,
            f"Could not send #{event.id}: {item.get('error')} — "
            "check SARVAM_API_KEY and the logs.",
        )
    else:
        messages.info(request, f"#{event.id} was not sent ({item.get('status')}).")
    return redirect("notifier:dashboard")


@require_POST
def dispatch_all_due(request):
    """Dispatch every due reminder in one confirm-gated run."""
    report = dispatch_due_reminders(confirm=True)
    messages.info(
        request,
        f"Dispatch run: {report.get('dispatched', 0)} sent, "
        f"{report.get('failed', 0)} failed, {report.get('skipped', 0)} skipped.",
    )
    return redirect("notifier:dashboard")


def respond(request, event_id):
    """Client-side reply page — the press-1 / press-2 loop.

    Demo-mode stand-in: in production an IVR webhook or a WhatsApp reply
    handler posts the same `digit` payload into `record_response`.
    """
    event = get_object_or_404(Event, pk=event_id)
    if request.method == "POST":
        result = record_response(event.id, request.POST.get("digit", ""), channel="demo_page")
        if result["ok"]:
            messages.success(request, result["message"])
        else:
            messages.error(request, result["message"])
        return redirect("notifier:respond", event_id=event.id)
    return render(request, "notifier/respond.html", {"event": event})


def _webhook_token(request):
    """Extract the caller's webhook token from Bearer / X-Api-Key / params."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("X-Api-Key", "")
        or request.POST.get("token", "")
        or request.GET.get("token", "")
    )


@csrf_exempt
@require_POST
def response_webhook(request):
    """Inbound replies from real channels: IVR DTMF digits, WhatsApp bots.

    Auth: the BOLO_WEBHOOK_TOKEN env value as a Bearer token or X-Api-Key
    header (or `token` parameter). The endpoint stays disabled (403) until
    that variable is set, so nothing is ever open by accident.

    Body (JSON or form-encoded): {"event_id": 123, "digit": "1"|"2",
    "channel": "ivr"} - the same record_response entry point the demo
    respond page uses.
    """
    expected = os.getenv("BOLO_WEBHOOK_TOKEN", "")
    if not expected:
        return JsonResponse(
            {
                "ok": False,
                "error": "webhook_disabled",
                "message": "Set BOLO_WEBHOOK_TOKEN in the environment to enable this endpoint.",
            },
            status=403,
        )
    if not hmac.compare_digest(str(expected), str(_webhook_token(request))):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    if request.content_type == "application/json":
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    else:
        data = request.POST

    try:
        event_id = int(data.get("event_id"))
    except (TypeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "invalid_event_id",
             "message": "Body needs an integer event_id."},
            status=400,
        )

    channel = str(data.get("channel") or "webhook").strip()[:20] or "webhook"
    result = record_response(event_id, data.get("digit", ""), channel=channel)
    return JsonResponse(result, status=200 if result["ok"] else 400)


def analytics(request):
    """Response analytics: the proof that vernacular reminders get answered.

    Everything is computed live from the Event table — no separate tracking
    tables, no snapshots to keep in sync.
    """
    language_names = dict(LANGUAGES)
    per_language = {}
    totals = {"dispatched": 0, "confirmed": 0, "declined": 0, "pending": 0}

    dispatched = Event.objects.filter(
        voice_dispatched_at__isnull=False, cancelled_at__isnull=True
    ).select_related("person")
    for event in dispatched:
        code = event.person.preferred_language
        entry = per_language.setdefault(
            code, {"language": language_names.get(code, code), "dispatched": 0,
                   "confirmed": 0, "declined": 0, "pending": 0}
        )
        entry["dispatched"] += 1
        totals["dispatched"] += 1
        if event.response_value == Event.ResponseValue.CONFIRMED:
            entry["confirmed"] += 1
            totals["confirmed"] += 1
        elif event.response_value == Event.ResponseValue.DECLINED:
            entry["declined"] += 1
            totals["declined"] += 1
        else:
            entry["pending"] += 1
            totals["pending"] += 1

    languages = sorted(per_language.values(), key=lambda row: -row["dispatched"])
    for row in languages:
        replied = row["confirmed"] + row["declined"]
        row["replied"] = replied
        row["response_rate"] = round(100 * replied / row["dispatched"]) if row["dispatched"] else 0

    replied_total = totals["confirmed"] + totals["declined"]
    return render(request, "notifier/analytics.html", {
        "languages": languages,
        "totals": totals,
        "replied_total": replied_total,
        "response_rate": round(100 * replied_total / totals["dispatched"]) if totals["dispatched"] else 0,
    })
