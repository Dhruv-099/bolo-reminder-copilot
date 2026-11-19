"""Cron-friendly auto-dispatch for due voice reminders.

Runs once and exits by default — schedule it with cron / Windows Task
Scheduler. With --loop-minutes N the process stays alive and dispatches every
N minutes, which is handy for a local demo machine.
"""

import sys
import time

from django.core.management.base import BaseCommand

from notifier.services.reminder_dispatch import dispatch_due_reminders, render_report


class Command(BaseCommand):
    help = (
        "Auto-dispatch due voice reminders (cron/Task Scheduler friendly; "
        "--loop-minutes N to keep running)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually dispatch (mark events) instead of previewing.",
        )
        parser.add_argument(
            "--loop-minutes",
            type=int,
            default=0,
            help="Keep running and dispatch every N minutes; 0 (default) = single run.",
        )
        parser.add_argument(
            "--event-ids",
            type=str,
            default="",
            help="Comma-separated event ids to scope the run.",
        )

    def handle(self, *args, **options):
        # Windows consoles often default to a legacy code page that cannot
        # print Indic scripts; force UTF-8 so reports render everywhere.
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8")

        event_ids = [part for part in options["event_ids"].split(",") if part.strip().isdigit()]

        def run_once():
            report = dispatch_due_reminders(confirm=options["confirm"], event_ids=event_ids or None)
            self.stdout.write(render_report(report))
            if report.get("failed"):
                self.stderr.write(
                    self.style.ERROR(
                        f"{report['failed']} reminder(s) failed - check that "
                        "SARVAM_API_KEY is set and Sarvam is reachable."
                    )
                )

        run_once()

        if options["loop_minutes"] > 0:
            self.stdout.write(
                self.style.NOTICE(
                    f"Looping every {options['loop_minutes']} minute(s). Press Ctrl+C to stop."
                )
            )
            try:
                while True:
                    time.sleep(options["loop_minutes"] * 60)
                    run_once()
            except KeyboardInterrupt:
                self.stdout.write(self.style.NOTICE("Stopped."))
