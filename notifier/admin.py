from django.contrib import admin
from .models import Person, Event
# Register your models here.

@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone_number', 'email', 'preferred_language', 'channel_preference')
    search_fields = ('name', 'phone_number', 'email')
    list_filter = ('preferred_language', 'channel_preference')

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('person', 'event_type', 'event_datetime', 'recurrence', 'voice_dispatched_at', 'response_value')
    list_filter = ('event_type', 'response_value', 'recurrence', 'event_datetime')
    search_fields = ('person__name','details')
