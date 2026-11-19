
from vonage import Vonage as VonageClient, Auth
from vonage_sms import SmsMessage
from django.conf import settings
from django.urls import reverse
import logging

logger = logging.getLogger(__name__)

# def get_vonage_client():
#     return vonage.Client(key=settings.VONAGE_API_KEY, secret=settings.VONAGE_API_SECRET)
    
_vonage_sdk_instance = None

def get_vonage_sdk_instance():
    """
    Initializes and returns a global Vonage SDK instance using the new Auth method.
    """
    global _vonage_sdk_instance
    if _vonage_sdk_instance is None:
        if not all([settings.VONAGE_API_KEY, settings.VONAGE_API_SECRET]):
            logger.error("Vonage API key or secret not configured.")
            return None
        try:
            auth = Auth(api_key=settings.VONAGE_API_KEY, api_secret=settings.VONAGE_API_SECRET)
            # HttpClientOptions can be added here if needed
            _vonage_sdk_instance = VonageClient(auth=auth)
            logger.info("Vonage SDK instance initialized successfully (New SDK Structure).")
        except Exception as e:
            logger.error(f"Failed to initialize Vonage SDK instance: {e}")
            return None
    return _vonage_sdk_instance

def send_sms_via_vonage(to_number, message_body):
    vonage_sdk = get_vonage_sdk_instance()
    sms_data = SmsMessage(
            to=to_number,
            from_=settings.VONAGE_PHONE_NUMBER,
            text=message_body
        )

    response_data = vonage_sdk.sms.send(sms_data)
    if response_data["messages"][0]["status"] == "0":
        logger.info(f"SMS sent to {to_number}. ID: {response_data['messages'][0]['message-id']}")
        return True
    else:
        logger.error(f"Failed to send SMS to {to_number}: {response_data['messages'][0]['error-text']}")
        return False
    

def make_interactive_call_via_vonage(to_number,event_id):
    client = get_vonage_client()
    if not client or not settings.VONAGE_PHONE_NUMBER or not settings.YOUR_NGROK_OR_PUBLIC_DOMAIN:
        logger.error("Vonage client, phone number, or public domain not available for interactive call.")
        return None
    try:
        voice = vonage.Voice(client)
        base_url = settings.YOUR_NGROK_OR_PUBLIC_DOMAIN
        answer_path = reverse('notifier:vonage_answer') # Ensure 'notifier' is app_name in notifier/urls.py
        full_answer_url = f"{base_url}{answer_path}?event_id={event_id}"
        
        # Optional: A general event URL for call status updates (hangup, busy, etc.)
        # event_path = reverse('notifier:vonage_call_event')
        # full_event_url = f"{base_url}{event_path}"

        logger.info(f"Initiating interactive call to {to_number} with answer_url: {full_answer_url}")
        response = voice.create_call({
            'to': [{'type': 'phone', 'number': to_number}],
            'from': {'type': 'phone', 'number': settings.VONAGE_PHONE_NUMBER},
            'answer_url': [full_answer_url],
            # 'event_url': [full_event_url] # Optional
        })
        logger.info(f"Interactive call initiated to {to_number}. UUID: {response['uuid']}")
        return response['uuid']
    except Exception as e:
        logger.error(f"Error making interactive call via Vonage: {e}")
        return None