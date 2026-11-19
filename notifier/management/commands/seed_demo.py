"""Seed the fictional demo people and events when the database is empty.

Runs on container start (see the Dockerfile CMD) so a fresh stack comes up
demo-ready without a manual exec step. Existing data is never touched; pass
--force to reset and reseed - the same reset create_synthetic_data.py does
when run by hand.
"""

import sys

from django.core.management.base import BaseCommand

from notifier.models import Person


class Command(BaseCommand):
    help = "Seed fictional demo people/events when the database is empty; --force resets first."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reset existing data first (deletes all people and events).",
        )

    def handle(self, *args, **options):
        # Windows consoles often default to a legacy code page that cannot
        # print Indic scripts; force UTF-8 so reports render everywhere.
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8")

        # Imported lazily: the module calls django.setup() at import time.
        from create_synthetic_data import clear_data, create_events, create_people

        if options["force"]:
            clear_data()
        elif Person.objects.exists():
            self.stdout.write(
                self.style.NOTICE("Demo data already present - skipping seed (use --force to reset).")
            )
            return

        people = create_people()
        if people:
            create_events(people)
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
