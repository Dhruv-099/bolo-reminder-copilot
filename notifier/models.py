import re

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
    class ChannelPreference(models.TextChoices):
        VOICE_CALL = 'VOICE_CALL', 'Voice call'
        WHATSAPP = 'WHATSAPP', 'WhatsApp'
        SMS = 'SMS', 'SMS'
        DEMO_PAGE = 'DEMO_PAGE', 'Demo page (prototype)'

    name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True, null=True)
    preferred_language = models.CharField(
        max_length=5,
        choices=LANGUAGES,
        default="en-IN",
        help_text="BCP-47 code used to translate and voice this person's reminders.",
    )
    channel_preference = models.CharField(
        max_length=20,
        choices=ChannelPreference.choices,
        default=ChannelPreference.WHATSAPP,
        help_text="Where this person prefers to receive reminders.",
    )
    quiet_hours_start = models.TimeField(
        blank=True,
        null=True,
        help_text="Reminders are held between quiet_hours_start and quiet_hours_end (person's local clock).",
    )
    quiet_hours_end = models.TimeField(
        blank=True,
        null=True,
        help_text="End of the quiet window. May wrap past midnight (e.g. 22:00 to 08:00).",
    )

    def save(self, *args, **kwargs):
        self.phone_number = self.normalize_phone(self.phone_number)
        super().save(*args, **kwargs)

    @staticmethod
    def normalize_phone(raw):
        """Best-effort E.164 normalization; bare 10-digit numbers default to +91."""
        if not raw:
            return raw
        digits = re.sub(r"\D", "", raw)
        if raw.startswith("+") and digits:
            return "+" + digits
        if len(digits) == 10:
            return "+91" + digits
        if len(digits) == 12 and digits.startswith("91"):
            return "+91" + digits[2:]
        return raw

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
    class ResponseValue(models.TextChoices):
        PENDING = 'PENDING', 'Awaiting reply'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        DECLINED = 'DECLINED', 'Declined'
    class Recurrence(models.TextChoices):
        NONE = 'NONE', 'One-off'
        DAILY = 'DAILY', 'Daily'
        WEEKLY = 'WEEKLY', 'Weekly'
        MONTHLY = 'MONTHLY', 'Monthly'
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
    voice_transcript_english = models.TextField(
        blank=True,
        default="",
        help_text="Personalized English text that was translated and spoken.",
    )
    voice_transcript_local = models.TextField(
        blank=True,
        default="",
        help_text="Translated text actually spoken in the person's language.",
    )
    response_value = models.CharField(
        max_length=10,
        choices=ResponseValue.choices,
        default=ResponseValue.PENDING,
        help_text="Client reply captured via phone keypad, WhatsApp or the demo page.",
    )
    response_received_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When the client's reply was recorded.",
    )
    response_channel = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Where the reply came from (demo_page, whatsapp, ivr).",
    )
    cancelled_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Set when the reminder was cancelled; cancelled events are never sent.",
    )
    dispatch_attempts = models.PositiveSmallIntegerField(
        default=0,
        help_text="How many times this reminder has been dispatched (retry ladder).",
    )
    recurrence = models.CharField(
        max_length=10,
        choices=Recurrence.choices,
        default=Recurrence.NONE,
        help_text="How often this event repeats.",
    )
    recurrence_parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='recurrences',
        help_text="For generated occurrences: the event they were spawned from.",
    )

    def __str__(self):
        return f"{self.get_event_type_display()} for {self.person.name} at {self.event_datetime}"

    @property
    def is_notification_due(self):
        if self.cancelled_at:
            return False
        if self.sms_sent_at or self.call_made_at or self.email_sent_at or self.voice_dispatched_at:
            return False
        notification_trigger_time = self.event_datetime-timezone.timedelta(minutes=self.notification_lead_time_minutes)
        return timezone.now()>= notification_trigger_time
