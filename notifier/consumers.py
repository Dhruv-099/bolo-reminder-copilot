"""WebSocket consumer for the live dispatch feed.

The dashboard connects to /ws/dispatch/ and receives progress events pushed
by the dispatch workflow (reminder_dispatch._emit_progress): one line per
event as it is drafted, held, dispatched or skipped, plus run start and end.
"""

from channels.generic.websocket import AsyncJsonWebsocketConsumer

DISPATCH_GROUP = "bolo.dispatch"


class DispatchProgressConsumer(AsyncJsonWebsocketConsumer):
    """Forwards channel-layer group events to connected dashboards as JSON."""

    async def connect(self):
        await self.channel_layer.group_add(DISPATCH_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(DISPATCH_GROUP, self.channel_name)

    async def dispatch_progress(self, event):
        await self.send_json(event["payload"])
