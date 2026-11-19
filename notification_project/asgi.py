"""
ASGI config for the Bolo reminder copilot.

Serves the Django app over HTTP and the live dispatch feed over WebSocket
(/ws/dispatch/): the dashboard subscribes and receives progress events pushed
by the dispatch workflow while it drafts, holds, dispatches or skips each
reminder.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'notification_project.settings')

django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path

from notifier.consumers import DispatchProgressConsumer

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter([
            path('ws/dispatch/', DispatchProgressConsumer.as_asgi()),
        ])
    ),
})
