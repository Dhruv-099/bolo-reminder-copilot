
from django.urls import path
from . import views

app_name = 'notifier'  # Important for namespacing (e.g., notifier:vonage_answer)

urlpatterns = [
    path('vonage/answer/', views.handle_answered_call, name='vonage_answer'),
    path('vonage/dtmf_event/', views.handle_dtmf_input, name='vonage_dtmf_event'),
    # path('vonage/call_event/', views.handle_call_event, name='vonage_call_event'), # Optional
]