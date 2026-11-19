# create_synthetic_data.py
"""Seed the demo with fictional people and due reminder events.

Every person carries a preferred language; the dispatch workflow translates
and voices each reminder accordingly. Event times are relative to "now", so
running this script always leaves the system demo-ready. It resets existing
demo data first.
"""
import os
from datetime import timedelta

import django

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notification_project.settings")
django.setup()

from django.utils import timezone

from notifier.models import Event, Person


def clear_data():
    """Clears existing demo data so every run starts fresh."""
    print("Clearing existing demo data...")
    Event.objects.all().delete()
    Person.objects.all().delete()
    print("Data cleared.")


def create_people():
    """Creates fictional people across the supported reminder languages."""
    people_data = [
        {"name": "Rahul Verma", "phone_number": "+919000000001", "email": "rahul@example.com", "preferred_language": "hi-IN"},
        {"name": "Arjun Reddy", "phone_number": "+919000000002", "email": "arjun@example.com", "preferred_language": "te-IN"},
        {"name": "Priya Iyer", "phone_number": "+919000000003", "email": "priya@example.com", "preferred_language": "ta-IN"},
        {"name": "Sneha Patil", "phone_number": "+919000000004", "email": "sneha@example.com", "preferred_language": "mr-IN"},
        {"name": "Ananya Rao", "phone_number": "+919000000005", "email": "ananya@example.com", "preferred_language": "kn-IN"},
        {"name": "John Dsouza", "phone_number": "+919000000006", "email": "john@example.com", "preferred_language": "en-IN"},
    ]
    created_people = []
    print("Creating fictional people...")
    for data in people_data:
        person, _ = Person.objects.get_or_create(phone_number=data["phone_number"], defaults=data)
        print(f"  Created {person.name} (reminders in {person.preferred_language})")
        created_people.append(person)
    return created_people


def create_events(people):
    """Creates reminder events with times relative to now.

    Most events are due immediately; two are deliberately not due yet, so the
    dispatch preview has a real filter to respect.
    """
    now = timezone.now()
    print("\nCreating events...")

    event_definitions = [
        # --- Rahul (Hindi) ---
        {
            "person_index": 0, "event_type": Event.EventType.MEETING,
            "event_datetime_delta": timedelta(minutes=75),
            "message_template": "Hi {name}, reminder: your '{details}' {event_type_display} is {time_left} at {time} on {date}.",
            "details": "Project Alpha Sync", "notification_lead_time_minutes": 120,
        },
        {
            "person_index": 0, "event_type": Event.EventType.BIRTHDAY,
            "event_datetime_delta": timedelta(days=2),
            "message_template": "Hi {name}! Your {event_type_display} is {time_left}. We will send a special greeting.",
            "details": "Birthday Celebration",
            "notification_lead_time_minutes": 24 * 60,  # not due yet
        },
        # --- Arjun (Telugu) ---
        {
            "person_index": 1, "event_type": Event.EventType.WEBINAR,
            "event_datetime_delta": timedelta(hours=3),
            "message_template": "{name}, your {event_type_display} on '{details}' starts {time_left}, at {time}.",
            "details": "Future of AI", "notification_lead_time_minutes": 240,
        },
        {
            "person_index": 1, "event_type": Event.EventType.REMINDER,
            "event_datetime_delta": timedelta(minutes=30),
            "message_template": "Quick reminder for {name}: '{details}' is {time_left}.",
            "details": "Submit timesheet", "notification_lead_time_minutes": 45,
        },
        # --- Priya (Tamil) ---
        {
            "person_index": 2, "event_type": Event.EventType.WORKSHOP,
            "event_datetime_delta": timedelta(minutes=45),
            "message_template": "{name}, your workshop '{details}' begins {time_left}, at {time} on {date}.",
            "details": "Advanced Python", "notification_lead_time_minutes": 60,
        },
        # --- Sneha (Marathi) ---
        {
            "person_index": 3, "event_type": Event.EventType.SOCIAL_EVENT,
            "event_datetime_delta": timedelta(minutes=5),
            "message_template": "Hey {name}! The {event_type_display} '{details}' is starting {time_left}! Hope you can make it.",
            "details": "Team Lunch", "notification_lead_time_minutes": 10,
        },
        # --- Ananya (Kannada) ---
        {
            "person_index": 4, "event_type": Event.EventType.CONFERENCE,
            "event_datetime_delta": timedelta(days=7),
            "message_template": "Greetings {name}, the {event_type_display} '{details}' is {time_left}.",
            "details": "DevCon 2026",
            "notification_lead_time_minutes": 3 * 24 * 60,  # not due yet
        },
        # --- John (English) ---
        {
            "person_index": 5, "event_type": Event.EventType.REMINDER,
            "event_datetime_delta": timedelta(minutes=90),
            "message_template": "{name}, a gentle reminder: '{details}' is due {time_left}, at {time}.",
            "details": "Quarterly report submission", "notification_lead_time_minutes": 120,
        },
    ]

    for data in event_definitions:
        person = people[data["person_index"]]
        event_datetime = now + data["event_datetime_delta"]

        Event.objects.create(
            person=person,
            event_type=data["event_type"],
            event_datetime=event_datetime,
            message_template=data["message_template"],
            details=data.get("details", ""),
            notification_lead_time_minutes=data["notification_lead_time_minutes"],
        )
        print(f"  Created {data['event_type']} for {person.name} at {event_datetime.strftime('%Y-%m-%d %H:%M')}")


def main():
    """Resets and reseeds the demo data."""
    clear_data()
    created_people = create_people()
    if created_people:
        create_events(created_people)
    print("\nDemo data ready. Run 'python manage.py send_notifications' to preview due reminders.")


if __name__ == "__main__":
    main()
