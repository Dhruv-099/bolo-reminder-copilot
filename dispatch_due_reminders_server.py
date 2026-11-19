"""
Bolo MCP server: the agent's tool surface for the reminder copilot.

`dispatch_due_reminders` was composed by Synapse (v2 Custom mode) — its body is
a thin pass-through of notifier.services.reminder_dispatch, and per Synapse this
file is ours to extend. The tools below were hand-added on the same FastMCP
scaffold:

- dispatch_due_reminders   — the confirm-gated composed workflow
- add_reminder             — conversational intake (creates Person + Event)
- reschedule_event         — move one; resets send state if already dispatched
- cancel_reminder          — cancelled events are never sent
- list_responses           — who confirmed / declined / is still silent
- reminder_digest          — morning round: due today, replies, retries

Every tool is declared async and runs its sync (Django ORM) body in a worker
thread via run_sync: FastMCP calls sync tool functions directly on its
asyncio event loop, and Django raises SynchronousOnlyOperation from there.

Run:  python dispatch_due_reminders_server.py
"""
from __future__ import annotations

import functools

import anyio
from mcp.server.fastmcp import FastMCP

from notifier.services.reminder_dispatch import dispatch_due_reminders  # bootstraps Django
from notifier.services import agent_tools

mcp = FastMCP("bolo")


async def run_sync(func, /, **kwargs):
    """Await a sync Django-ORM callable on a worker thread (see module docstring)."""
    return await anyio.to_thread.run_sync(functools.partial(func, **kwargs))


@mcp.tool(
    name="dispatch_due_reminders",
    description=(
        "Composed workflow to find due reminder events, personalize each message, "
        "translate it into each person's preferred language, and generate voice "
        "audio. confirm=false previews drafts and changes nothing; confirm=true "
        "records dispatch timestamps and skips already-dispatched events. Pass "
        "event_ids (list of integers) to scope the run to specific events."
    ),
)
async def dispatch_due_reminders_tool(confirm: bool = False, event_ids: list[int] | None = None) -> dict:
    """Preview (confirm=false) or dispatch (confirm=true) due reminders."""
    if not isinstance(confirm, bool):
        return {"error": "confirm must be a bool"}
    if event_ids is not None and not isinstance(event_ids, list):
        return {"error": "event_ids must be a list of integers"}
    return await run_sync(dispatch_due_reminders, confirm=confirm, event_ids=event_ids)


@mcp.tool(
    name="add_reminder",
    description=(
        "Schedule a reminder for a person (creates or reuses them by phone number). "
        "When no message_template is given, one is drafted by an LLM. 'when' is ISO "
        "local time like '2026-11-21 16:00' (a bare date means 09:00). Nothing is "
        "sent now — dispatch_due_reminders delivers the voice reminder when its lead "
        "time arrives."
    ),
)
async def add_reminder_tool(
    person_name: str,
    phone_number: str,
    what: str,
    when: str,
    preferred_language: str = "en-IN",
    event_type: str = "REMINDER",
    lead_minutes: int = 120,
    details: str = "",
    message_template: str | None = None,
    person_email: str | None = None,
) -> dict:
    """Conversational intake: 'remind Ravi about his dental appointment Friday 4pm, in Telugu'."""
    return await run_sync(
        agent_tools.add_reminder,
        person_name=person_name,
        phone_number=phone_number,
        what=what,
        when=when,
        preferred_language=preferred_language,
        event_type=event_type,
        lead_minutes=lead_minutes,
        details=details,
        message_template=message_template,
        person_email=person_email,
    )


@mcp.tool(
    name="reschedule_event",
    description=(
        "Move a scheduled reminder to a new time ('when' is ISO local time, e.g. "
        "'2026-11-22 10:30'). If the voice reminder was already dispatched, its "
        "send state and the client's old reply are reset so it goes out fresh "
        "for the new slot."
    ),
)
async def reschedule_event_tool(event_id: int, when: str, note: str = "") -> dict:
    return await run_sync(agent_tools.reschedule_event, event_id=event_id, when=when, note=note)


@mcp.tool(
    name="cancel_reminder",
    description=(
        "Cancel a reminder. Cancelled events are never sent and disappear from "
        "all due/dispatch runs."
    ),
)
async def cancel_reminder_tool(event_id: int, reason: str = "") -> dict:
    return await run_sync(agent_tools.cancel_reminder, event_id=event_id, reason=reason)


@mcp.tool(
    name="list_responses",
    description=(
        "Follow-up view over dispatched reminders: who confirmed (press 1), who "
        "declined (press 2), who is still silent. Optional status filter: "
        "'confirmed', 'declined' or 'pending'."
    ),
)
async def list_responses_tool(status: str | None = None) -> dict:
    return await run_sync(agent_tools.list_responses, status=status)


@mcp.tool(
    name="reminder_digest",
    description=(
        "One-call summary for a morning round: reminders due today, replies still "
        "awaited, and dispatched reminders eligible for a retry nudge."
    ),
)
async def reminder_digest_tool() -> dict:
    return await run_sync(agent_tools.reminder_digest)


if __name__ == "__main__":
    mcp.run()
