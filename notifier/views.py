# notifier/views.py
from django.shortcuts import render


def home_page_view(request):
    context = {
        "project_name": "Bolo — Multilingual Voice Reminder Copilot",
        "description": "Event reminders drafted by an agent, spoken in each person's own language.",
    }
    return render(request, "notifier/home.html", context)
