"""Management command wrapper around the composable dispatch workflow.

Previews due reminders by default. Pass --confirm to actually dispatch, and
--event-ids 3,7 to scope the run to specific events.
"""

from django.core.management.base import BaseCommand

from notifier.services.reminder_dispatch import dispatch_due_reminders, render_report


class Command(BaseCommand):
    help = "Find due reminder events, draft multilingual voice reminders, and dispatch them with --confirm."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually dispatch (mark events) instead of previewing.",
        )
        parser.add_argument(
            "--event-ids",
            type=str,
            default="",
            help="Comma-separated event ids to scope the run.",
        )

    def handle(self, *args, **options):
        event_ids = [part for part in options["event_ids"].split(",") if part.strip().isdigit()]
        report = dispatch_due_reminders(confirm=options["confirm"], event_ids=event_ids or None)
        self.stdout.write(render_report(report))
        if report.get("failed"):
            self.stderr.write(
                self.style.ERROR(
                    f"{report['failed']} reminder(s) failed - check that SARVAM_API_KEY "
                    "is set and Sarvam is reachable."
                )
            )
