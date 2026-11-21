import os

from django.utils import timezone

def public_base_url():
    """Externally reachable base URL for links/webhooks (env-tunable)."""
    return os.getenv("BOLO_PUBLIC_BASE_URL", "http://192.168.1.15:8000").rstrip("/")


def respond_url(event_id):
    """Link a client taps to hear the reminder and press 1 / 2."""
    return f"{public_base_url()}/notifier/respond/{event_id}/"

def format_time_left(event_datetime):
    """
    Calculates the time remaining until event_datetime and formats it
    into a human-readable string.
    """
    now = timezone.now()
    delta = event_datetime - now

    if delta.total_seconds() <= 0:
        return "happening now or has passed"

    parts = []
    days = delta.days
    seconds = delta.seconds

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        parts.append(f"{days} day{'s' if days > 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    # Show minutes if it's less than a day away or if it's the only unit
    if minutes > 0 and (days == 0 or (days == 0 and hours == 0)):
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    
    if not parts: # Should ideally not happen if delta.total_seconds > 0
        return "very soon"
    
    time_left_str = "in "
    if len(parts) == 1:
        time_left_str += parts[0]
    elif len(parts) == 2:
        time_left_str += f"{parts[0]} and {parts[1]}"
    else: # len(parts) == 3
        time_left_str += f"{parts[0]}, {parts[1]}, and {parts[2]}"
    
    return time_left_str