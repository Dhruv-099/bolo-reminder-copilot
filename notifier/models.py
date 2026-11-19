from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

# BCP-47 codes supported by both the Sarvam Translate and Bulbul TTS APIs.
LANGUAGES = [
    ("en-IN", "English"),
    ("hi-IN", "Hindi"),
    ("bn-IN", "Bengali"),
    ("gu-IN", "Gujarati"),
    ("kn-IN", "Kannada"),
    ("ml-IN", "Malayalam"),
    ("mr-IN", "Marathi"),
    ("od-IN", "Odia"),
    ("pa-IN", "Punjabi"),
    ("ta-IN", "Tamil"),
    ("te-IN", "Telugu"),
]

class Person(models.Model):
    """
    Model to store people to remind.
    """
    name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    preferred_language = models.CharField(
        max_length=5,
        choices=LANGUAGES,
        default="en-IN",
        help_text="BCP-47 code used to translate and voice this person's reminders.",
    )

    def __str__(self):
        return self.name


class Event(models.Model):
    class EventType(models.TextChoices):
        BIRTHDAY = 'BIRTHDAY', 'Birthday'
        MEETING = 'MEETING', 'Meeting'
        REMINDER = 'REMINDER', 'Generic Reminder'
        CONFERENCE = 'CONFERENCE', 'Conference'
        WEBINAR = 'WEBINAR', 'Webinar'
        WORKSHOP = 'WORKSHOP', 'Workshop'
        SOCIAL_EVENT = 'SOCIAL_EVENT', 'Social Event'
        ANNIVERSARY = 'ANNIVERSARY', 'Anniversary'
        HOLIDAY = 'HOLIDAY', 'Holiday'
        OTHER = 'OTHER', 'Other'
    person=models.ForeignKey(Person, on_delete=models.CASCADE, related_name='events')
    event_type=models.CharField(max_length=20, choices=EventType.choices)
    event_datetime=models.DateTimeField()
    message_template=models.TextField(help_text="")
    details=models.TextField(blank=True, null=True)
    notification_lead_time_minutes=models.PositiveBigIntegerField(default=120)
    sms_sent_at=models.DateTimeField(blank=True, null=True)
    call_made_at=models.DateTimeField(blank=True, null=True)
    email_sent_at=models.DateTimeField(blank=True, null=True)
    vonage_call_uuid = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    voice_dispatched_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Set when the multilingual voice reminder was dispatched.",
    )
    voice_audio_path = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Generated audio path, relative to MEDIA_ROOT.",
    )

    def __str__(self):
        return f"{self.get_event_type_display()} for {self.person.name} at {self.event_datetime}"

    @property
    def is_notification_due(self):
        if self.sms_sent_at or self.call_made_at or self.email_sent_at or self.voice_dispatched_at:
            return False
        notification_trigger_time = self.event_datetime-timezone.timedelta(minutes=self.notification_lead_time_minutes)
        return timezone.now()>= notification_trigger_time
