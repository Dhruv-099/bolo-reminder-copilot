"""SMS delivery via MSG91 (India).

Uses MSG91's v5 flow API: the message text and the respond link fill the
variables of your DLT-registered template (VAR1/VAR2 by default - adjust to
your template's variable layout). Failure-safe: a missing auth key or a
failed call returns {"ok": False, ...} and never raises.
"""

import logging
import os

import requests

from notifier.utils import respond_url

logger = logging.getLogger(__name__)

MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow/"


def _msg91_configured():
    return bool(os.getenv("MSG91_AUTH_KEY") and os.getenv("MSG91_FLOW_ID"))


def send_reminder_sms(event, package):
    """Text the reminder in the person's language, with the respond link.

    The link opens the client reply page (audio + press-1/press-2), so the
    confirmation loop closes over plain SMS with no app required.
    """
    if not _msg91_configured():
        return {"ok": False, "error": "not_configured"}

    text = package.get("text_local") or package.get("text_english") or ""
    link = respond_url(event.id)
    payload = {
        "flow_id": os.getenv("MSG91_FLOW_ID"),
        # MSG91 expects the number without the leading plus.
        "recipients": [
            {
                "mobiles": event.person.phone_number.lstrip("+"),
                "VAR1": text,
                "VAR2": link,
            }
        ],
    }
    headers = {"authkey": os.getenv("MSG91_AUTH_KEY"), "Content-Type": "application/json"}
    try:
        response = requests.post(MSG91_FLOW_URL, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("MSG91 send failed for event %s: %s", event.id, exc)
        return {"ok": False, "error": "send_failed", "detail": str(exc)}

    if str(data.get("type", "")).lower() != "success":
        logger.error("MSG91 rejected event %s: %s", event.id, data)
        return {"ok": False, "error": "send_rejected", "detail": data}
    return {"ok": True, "provider": "msg91", "message": text, "link": link}
