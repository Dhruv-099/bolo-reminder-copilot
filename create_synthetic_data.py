# create_synthetic_data.py
import os
import django
from datetime import timedelta

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'notification_project.settings')
django.setup()

from django.utils import timezone
from notifier.models import Person, Event # Your app and model names

def clear_data():
    """Clears existing Person and Event data."""
    print("Clearing existing Event data...")
    Event.objects.all().delete()
    print("Clearing existing Person data...")
    Person.objects.all().delete()
    print("Data cleared.")

def create_people():
    """Creates sample Person objects."""
    people_data = [
        {"name": "Rahul Verma", "phone_number": "+919000000001", "email": "rahul@example.com"},
        {"name": "Alice Wonderland", "phone_number": "+12005550101", "email": "alice@example.com"},
        {"name": "Bob The Builder", "phone_number": "+12005550102", "email": "bob@example.com"},
        {"name": "Charlie Brown", "phone_number": "+12005550103", "email": "charlie@example.com"},
        {"name": "Diana Prince", "phone_number": "+12005550104", "email": "diana@example.com"},
        {"name": "Edward Scissorhands", "phone_number": "+12005550105", "email": "edward@example.com"},
    ]
    created_people = []
    print("Creating Person objects...")
    for data in people_data:
        person, created = Person.objects.get_or_create(phone_number=data["phone_number"], defaults=data)
        if created:
            print(f"  Created Person: {person.name}")
        else:
            print(f"  Person {person.name} already exists.")
        created_people.append(person)
    return created_people

def create_events(people):
    """Creates sample Event objects for the given people."""
    now = timezone.now()
    print("\nCreating Event objects...")

    event_definitions = [
        {
            "person_index": 0, "event_type": Event.EventType.MEETING,
            "event_datetime_delta": timedelta(minutes=75), # Due in 1h 15m
            "message_template": "Hi {name}, reminder: Your '{details}' {event_type_display} is {time_left} at {time} on {date}. The call will present options.",
            "details": "Project Alpha Sync", "notification_lead_time_minutes": 60
        },
        # --- Events for Alice ---
        {
            "person_index": 0, "event_type": Event.EventType.MEETING,
            "event_datetime_delta": timedelta(minutes=75), # Due in 1h 15m
            "message_template": "Hi {name}, reminder: Your '{details}' {event_type_display} is {time_left} at {time} on {date}. The call will present options.",
            "details": "Project Alpha Sync", "notification_lead_time_minutes": 60
        },
        {
            "person_index": 0, "event_type": Event.EventType.BIRTHDAY,
            "event_datetime_delta": timedelta(days=2, hours=5), # Due in 2 days
            "message_template": "Hi {name}! Just a heads up, your {event_type_display} is {time_left}! We'll call with a special greeting. Happy Birthday soon!",
            "details": "Alice's Birthday Bash", "notification_lead_time_minutes": 24 * 60 # 1 day before
        },
        # --- Events for Bob ---
        {
            "person_index": 1, "event_type": Event.EventType.WEBINAR,
            "event_datetime_delta": timedelta(hours=3), # Due in 3 hours
            "message_template": "{name}, your {event_type_display} on '{details}' starts {time_left}. Dial in details will be on the call.",
            "details": "Future of AI", "notification_lead_time_minutes": 120 # 2 hours before
        },
        {
            "person_index": 1, "event_type": Event.EventType.REMINDER,
            "event_datetime_delta": timedelta(minutes=30), # Due in 30 mins
            "message_template": "Quick reminder for {name}: '{details}' is {time_left}. Call will follow.",
            "details": "Submit timesheet", "notification_lead_time_minutes": 15
        },
        # --- Events for Charlie ---
        {
            "person_index": 2, "event_type": Event.EventType.CONFERENCE,
            "event_datetime_delta": timedelta(days=7), # 1 week away
            "message_template": "Greetings {name}, the {event_type_display} '{details}' is {time_left}. More info will be provided on the interactive call.",
            "details": "DevCon 2025", "notification_lead_time_minutes": 3 * 24 * 60 # 3 days before
        },
        # --- Events for Diana (some further out) ---
        {
            "person_index": 3, "event_type": Event.EventType.WORKSHOP,
            "event_datetime_delta": timedelta(days=10, hours=2),
            "message_template": "{name}, your workshop '{details}' is {time_left}. Please prepare accordingly.",
            "details": "Advanced Python", "notification_lead_time_minutes": 7 * 24 * 60 # 1 week before
        },
        {
            "person_index": 3, "event_type": Event.EventType.ANNIVERSARY,
            "event_datetime_delta": timedelta(days=30),
            "message_template": "Hi {name}, your {event_type_display} is {time_left}! We'll be in touch.",
            "details": "Work Anniversary", "notification_lead_time_minutes": 5 * 24 * 60 # 5 days before
        },
        # --- Events for Edward (one very soon) ---
        {
            "person_index": 4, "event_type": Event.EventType.SOCIAL_EVENT,
            "event_datetime_delta": timedelta(minutes=5), # Due in 5 mins (will trigger immediately if lead time < 5)
            "message_template": "Hey {name}! The {event_type_display} '{details}' is starting {time_left}! Hope you can make it.",
            "details": "Team Lunch", "notification_lead_time_minutes": 10 # Lead time is 10 mins
        },
        {
            "person_index": 4, "event_type": Event.EventType.HOLIDAY,
            "event_datetime_delta": timedelta(days=1, hours=1),
            "message_template": "{name}, friendly reminder that the {event_type_display} '{details}' is {time_left}.",
            "details": "Office Closed for Public Holiday", "notification_lead_time_minutes": 24*60 # 1 day
        },
        # --- Event that should have passed notification time but not occurred (for testing edge cases) ---
        {
            "person_index": 0, "event_type": Event.EventType.OTHER,
            "event_datetime_delta": timedelta(minutes=45), # Event in 45 mins
            "message_template": "{name}, your '{details}' event is {time_left}. This is a test message.",
            "details": "System Check", "notification_lead_time_minutes": 60 # Lead time was 1 hour ago, so should be due
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
            details=data.get("details", f"Details for {data['event_type'].lower()}"), # Default details
            notification_lead_time_minutes=data["notification_lead_time_minutes"],
            # sms_sent_at and call_made_at will be None by default
        )
        print(f"  Created {data['event_type']} for {person.name} at {event_datetime.strftime('%Y-%m-%d %H:%M')}")

def main():
    """Main function to run the data creation."""
    # Optional: Clear existing data first if you want a fresh start each time
    # Be careful with this in a non-dev environment!
    # clear_data()

    created_people = create_people()
    if created_people:
        create_events(created_people)
    print("\nSynthetic data creation process finished.")

if __name__ == '__main__':
    main()