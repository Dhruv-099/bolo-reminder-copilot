from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from notifier.models import Event
from notifier.services.vonage_service import send_sms_via_vonage, make_interactive_call_via_vonage
from notifier.utils import format_time_left 
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Checks for due events and sends notifications (SMS and Interactive Call)'

    def handle(self, *args, **options):
        self.stdout.write(f"[{timezone.now()}] Checking for due notifications...")
        
        due_events_for_processing = []
        for event in Event.objects.filter(call_made_at__isnull=True): 
            if event.is_notification_due:
                due_events_for_processing.append(event)

        if not due_events_for_processing:
            self.stdout.write(self.style.SUCCESS("No due events found requiring notification."))
            return

        for event in due_events_for_processing:
            self.stdout.write(f"Processing event: {event.id} - {event.get_event_type_display()} for {event.person.name}")
            person = event.person
            
            
            time_left_str = format_time_left(event.event_datetime) # <-- but defined here

            # CORRECTED ORDER would put this line AFTER time_left_str is defined:
            personalized_message = event.message_template.format(
                name=person.name.split()[0], 
                event_type_display=event.get_event_type_display().lower(),
                details=event.details or "",
                time=event.event_datetime.strftime("%I:%M %p"), 
                date=event.event_datetime.strftime("%B %d, %Y"),
                time_left=time_left_str # Now it's defined
            ).strip()

            self.stdout.write(f"  Personalized Message: \"{personalized_message}\"")

            
            if not event.sms_sent_at:
                self.stdout.write(f"  Attempting to send SMS to {person.phone_number}...")
                success_sms = send_sms_via_vonage(person.phone_number, personalized_message)
                if success_sms:
                    event.sms_sent_at = timezone.now()
                    self.stdout.write(self.style.SUCCESS(f"  SMS sent for event {event.id}"))
                else:
                    logger.error(f"Failed to send SMS for event {event.id}")
                    self.stderr.write(self.style.ERROR(f"  Failed to send SMS for event {event.id}"))
            else:
                self.stdout.write(f"  SMS already sent for event {event.id} at {event.sms_sent_at}.")

            # Call
            if not event.call_made_at: 
                self.stdout.write(f"  Attempting to make INTERACTIVE call to {person.phone_number}...")
                if not settings.YOUR_NGROK_OR_PUBLIC_DOMAIN:
                    self.stderr.write(self.style.ERROR("  YOUR_NGROK_OR_PUBLIC_DOMAIN not set in .env. Cannot make interactive call."))
                    logger.error("YOUR_NGROK_OR_PUBLIC_DOMAIN not set for interactive call.")
                else:
                    call_uuid = make_interactive_call_via_vonage(person.phone_number)
                    if call_uuid:
                        event.call_made_at = timezone.now() # Mark that call was initiated
                        event.vonage_call_uuid = call_uuid # Store Vonage Call UUID
                        self.stdout.write(self.style.SUCCESS(f"  Interactive call initiated for event {event.id}. UUID: {call_uuid}"))
                    else:
                        logger.error(f"Failed to initiate interactive call for event {event.id}")
                        self.stderr.write(self.style.ERROR(f"  Failed to initiate interactive call for event {event.id}"))
            else:
                self.stdout.write(f"  Call already initiated for event {event.id} at {event.call_made_at}.")
            
            event.save() # Save changes 

        self.stdout.write(self.style.SUCCESS("Finished processing notifications batch."))