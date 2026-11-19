# notifier/views.py
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.urls import reverse
from django.shortcuts import get_object_or_404
import requests
import json
import logging
from .models import Event 
from .utils import format_time_left
# from .services.hf_models_service import text_to_speech_huggingface # If using custom TTS
from django.shortcuts import render 

def home_page_view(request):
    context = {
        "project_name": "Automated Notification System",
        "description": "This system handles event notifications and interactive calls.",
    }
    return render(request, 'notifier/home.html', context)
logger = logging.getLogger(__name__)


def call_langflow(input_text, session_id):
    
    logger.info(f"Calling Langflow (MOCKED) for session {session_id} with input: '{input_text}'")

    
    if not input_text: 
        return {
            "text_to_speak": "Welcome to our service. Press 1 for event details, press 2 to confirm attendance, or press 0 to repeat.",
            "is_final_message": False,
            "gather_next": True
        }
    elif input_text == "1":
        return {
            "text_to_speak": "You asked for event details. The event is a 'Meeting' on 'Tomorrow at 10 AM'. Press 0 to go back.",
            "is_final_message": False,
            "gather_next": True
        }
    elif input_text == "2":
        return {
            "text_to_speak": "Thank you for confirming your attendance. Goodbye!",
            "is_final_message": True,
            "gather_next": False
        }
    elif input_text == "0":
        return {
            "text_to_speak": "Welcome to our service. Press 1 for event details, press 2 to confirm attendance, or press 0 to repeat.",
            "is_final_message": False,
            "gather_next": True
        }
    else:
        return {
            "text_to_speak": "Sorry, that was an invalid option. Press 1 for event details, 2 to confirm, 0 to repeat.",
            "is_final_message": False,
            "gather_next": True
        }
    # --- END MOCK LANGFLOW LOGIC ---

    # # --- EXAMPLE OF A REAL LANGFLOW API CALL (NEEDS CUSTOMIZATION) ---
    # if not settings.LANGFLOW_API_ENDPOINT:
    #     logger.error("LANGFLOW_API_ENDPOINT not configured.")
    #     return {"text_to_speak": "Error: Langflow not configured.", "is_final_message": True, "gather_next": False}
    
    # payload = {
    #     settings.LANGFLOW_YOUR_INPUT_FIELD_NAME: input_text,
    #     settings.LANGFLOW_YOUR_SESSION_ID_FIELD_NAME: session_id,
    #     # Add other fields your Langflow flow might expect, like 'flow_id' or 'tweaks'
    # }
    # headers = {}
    # if settings.LANGFLOW_API_KEY:
    #     headers["Authorization"] = f"Bearer {settings.LANGFLOW_API_KEY}" # Or your specific auth scheme
    #     # headers["x-api-key"] = settings.LANGFLOW_API_KEY # Another common scheme
    
    # try:
    #     response = requests.post(settings.LANGFLOW_API_ENDPOINT, json=payload, headers=headers, timeout=10)
    #     response.raise_for_status() # Raise an exception for bad status codes
    #     langflow_data = response.json()
    
    #     # --- PARSE LANGFLOW RESPONSE (HIGHLY DEPENDENT ON YOUR FLOW'S OUTPUT STRUCTURE) ---
    #     # This is a common but very hypothetical structure. Inspect your Langflow output.
    #     # Example: result = langflow_data.get("outputs")[0].get("outputs").get(settings.LANGFLOW_YOUR_OUTPUT_FIELD_NAME)
    #     # Assuming 'result' is a dictionary like {"text_to_speak": "...", "is_final_message": false, "gather_next": true}
    #     # You need to adapt this parsing logic.
    #     # For simplicity, let's assume Langflow returns the exact structure we need:
    #     # parsed_response = result 
    
    #     # For now, using the mock structure as if it came from 'result'
    #     # This needs to be replaced by actual parsing of 'langflow_data'
    #     mock_parsed_response = {"text_to_speak": "Data from real Langflow.", "is_final_message": False, "gather_next": True}
    #     # if isinstance(langflow_data.get("result"), dict): # Adjust this condition
    #     #    mock_parsed_response = langflow_data["result"]
    
    #     # return mock_parsed_response # REPLACE with actual parsed response
    
    # except requests.exceptions.RequestException as e:
    #     logger.error(f"Error calling Langflow API: {e}")
    #     return {"text_to_speak": "Sorry, I'm having trouble connecting to my brain right now. Please try again later.", "is_final_message": True, "gather_next": False}
    # except (KeyError, TypeError, IndexError) as e:
    #     logger.error(f"Error parsing Langflow response: {e}. Response was: {langflow_data}")
    #     return {"text_to_speak": "Sorry, I received an unexpected response. Please try again later.", "is_final_message": True, "gather_next": False}
    
    
def generate_ncco(langflow_response_data, dtmf_event_url_for_next_input):
    ncco = []
    text_to_speak = langflow_response_data.get("text_to_speak", "An unexpected error occurred.")
    
    # TODO: Integrate Hugging Face TTS here if desired.
    # If using custom TTS:
    # 1. Generate audio: relative_audio_path = text_to_speech_huggingface(text_to_speak)
    # 2. Construct public_audio_url: public_audio_url = f"{settings.YOUR_NGROK_OR_PUBLIC_DOMAIN}{settings.MEDIA_URL}{relative_audio_path}"
    # 3. Use "stream" action: ncco.append({"action": "stream", "streamUrl": [public_audio_url]})
    # For now, using Vonage built-in TTS:
    ncco.append({
        "action": "talk",
        "text": text_to_speak,
        "voiceName": "Kimberly" 
    })

    if langflow_response_data.get("gather_next", False):
        ncco.append({
            "action": "input",
            "eventUrl": [dtmf_event_url_for_next_input],
            "maxDigits": 1, # Assuming single digit input for menu
            "timeOut": 10   # Seconds to wait for input
        })
    # If not gathering next and not explicitly hanging up, Vonage usually hangs up after talk.
    # else: # Optionally add explicit hangup
    #     ncco.append({"action": "hangup"})
    return ncco

@csrf_exempt
def handle_answered_call(request):
    call_uuid = request.POST.get('uuid') or request.GET.get('uuid') or request.data.get('uuid') # Vonage Call ID
    to_number = request.POST.get('to') or request.GET.get('to') or request.data.get('to') # Recipient
    logger.info(f"Call answered. UUID: {call_uuid}, To: {to_number}")
    langflow_response_data = call_langflow(input_text="", session_id=call_uuid)

    base_url = settings.YOUR_NGROK_OR_PUBLIC_DOMAIN
    dtmf_path = reverse('notifier:vonage_dtmf_event')
    dtmf_event_url_for_next_input = f"{base_url}{dtmf_path}?call_uuid={call_uuid}"
    
    ncco_response = generate_ncco(langflow_response_data, dtmf_event_url_for_next_input)
    return JsonResponse(ncco_response, safe=False)

@csrf_exempt
def handle_dtmf_input(request):
    try:
        if request.method == 'POST':
            data = json.loads(request.body.decode('utf-8'))
            dtmf_digit = data.get('dtmf', {}).get('digits')
            call_uuid = request.GET.get('call_uuid') 
            
            if not call_uuid and 'uuid' in data :
                call_uuid = data['uuid']
            logger.info(f"Received DTMF: '{dtmf_digit}' for call UUID: {call_uuid}")
            if not call_uuid:
                logger.error("Could not determine call_uuid for DTMF input.")
                return JsonResponse([{"action": "talk", "text": "Error: Call session lost."}], safe=False)
            langflow_response_data = call_langflow(input_text=dtmf_digit, session_id=call_uuid)
            base_url = settings.YOUR_NGROK_OR_PUBLIC_DOMAIN
            dtmf_path = reverse('notifier:vonage_dtmf_event')
            dtmf_event_url_for_next_input = f"{base_url}{dtmf_path}?call_uuid={call_uuid}" 
            ncco_response = generate_ncco(langflow_response_data, dtmf_event_url_for_next_input)
            return JsonResponse(ncco_response, safe=False)
        else:
            logger.warning(f"Received non-POST request for DTMF: {request.method}")
            return HttpResponse(status=405) 
            
    except json.JSONDecodeError:
        logger.error("Could not decode JSON from Vonage DTMF webhook.")
        return HttpResponse("Invalid JSON", status=400)
    except Exception as e:
        logger.error(f"Unexpected error in handle_dtmf_input: {e}", exc_info=True)
        return JsonResponse([{"action": "talk", "text": "An internal error occurred."}], safe=False)

@csrf_exempt
def handle_call_event(request):
    if request.method == 'POST':
        data = json.loads(request.body.decode('utf-8'))
        call_uuid = data.get('uuid')
        status = data.get('status')
        logger.info(f"General Call Event for UUID {call_uuid}: Status - {status}, Data: {data}")
        
    return HttpResponse(status=200)