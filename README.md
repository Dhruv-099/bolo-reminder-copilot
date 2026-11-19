# Bolo - Multilingual Voice Reminder Copilot

**2nd Brain Labs x Geek Room Vibe-a-thon - Track 4 (Open Innovation)**

Bolo turns an existing Django notification system into an agent-operated copilot
that reminds people about upcoming events **in their own language, as a voice
message**. A staff member asks an agent: *"who's due for a reminder?"* - the
agent lists the drafts, plays the generated Hindi / Telugu / Tamil / Marathi
audio, waits for a confirmation, dispatches, and never double-sends.

The existing application was not rewritten. Synapse (Custom mode) composed its
internal workflow functions into **one MCP tool** - `dispatch_due_reminders` -
in minutes. That tool is the only way the agent touches the system.

---

## The problem

Small Indian businesses - clinics, salons, coaching centres, NBFC collections
teams - lose money to no-shows and missed follow-ups. Two gaps make it worse:

1. **Language.** English reminders get ignored; vernacular ones get answered.
2. **Operability.** Reminder tools need trained staff clicking through admin
   screens; the people who know who needs reminding are rarely those people.

Bolo closes both: every person carries a `preferred_language`, and the dispatch
workflow is a conversation, not a form.

## How it works

The core of the app is a composable workflow
(`notifier/services/reminder_dispatch.py`):

1. **Find** - events whose reminder lead time has arrived and that have not
   been dispatched
2. **Personalize** - render each event's `message_template` (safe against odd
   templates)
3. **Translate** - colloquial translation via Sarvam's `mayura:v1`
   (`modern-colloquial` mode), 11 languages
4. **Voice** - Bulbul text-to-speech, saved as WAV under `media_root/reminders/`
5. **Dispatch & log** - only on confirmation: records timestamps and audio
   paths, idempotent (already-dispatched events are never re-sent)

`notifier/services/sarvam_service.py` is failure-safe: a missing key or an
unreachable API logs the error and returns `None`, so one bad event never
breaks the batch.

## The agent layer (why Synapse)

Synapse CLI scanned this codebase and composed the workflow above into a
single MCP tool:

```
dispatch_due_reminders(confirm=false, event_ids=null) -> report
```

- `confirm=false` - **preview**: drafts + audio for every due event, nothing
  persisted. The agent structurally cannot send anything without a human yes.
- `confirm=true` - **dispatch**: marks events, stores audio paths, skips
  anything already sent.
- Optional `event_ids` scopes the run ("yes, but skip Mr. Rao").

Because `confirm` gates every side effect, the safety story is in the tool
contract itself, not in prompt instructions.

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env             # then add your SARVAM_API_KEY
python manage.py migrate
python create_synthetic_data.py    # 6 fictional people, 6 due events
python manage.py send_notifications        # preview (no changes)
python manage.py send_notifications --confirm   # actually dispatch
```

The same workflow without any agent: `manage.py send_notifications` is a thin
wrapper around `dispatch_due_reminders` - proof the app still works exactly
as before, agent or no agent.

## Build the MCP server with Synapse

```bash
npm install -g @2ndbrainlabs-ai/synapse-cli     # needs Node.js 20+

# Local build (your own model key, no Synapse account, no quota):
set ANTHROPIC_API_KEY=sk-ant-...               # or another provider
synapse model                                   # verify provider detection
synapse build --local --custom --query "Compose a tool named dispatch_due_reminders that finds due Events, personalizes each message_template, translates to each Person's preferred_language via Sarvam, generates speech audio with Sarvam Bulbul, and only when confirm=true records dispatch timestamps and skips already-dispatched events. Use notifier.services.reminder_dispatch and notifier.services.sarvam_service."
```

This writes `dispatch_due_reminders_server.py` into the project root and
prints a client config snippet. (Hosted builds - `synapse init` then
`synapse build --custom` - work identically.)

### Connect an agent (OpenCode)

`opencode.jsonc` in the project root:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "bolo": {
        "type": "local",
        "command": ["python", "dispatch_due_reminders_server.py"],
        "environment": {
          "SARVAM_API_KEY": "{env:SARVAM_API_KEY}",
          "DJANGO_SETTINGS_MODULE": "notification_project.settings"
        }
      }
    }
  }
}
```

Verify with `opencode mcp list` (expects `connected`). The same server file
plugs into Claude Desktop or Cursor using the snippet Synapse prints.

## Demo script (~4 minutes)

| Beat | What happens |
|---|---|
| 1 | `python create_synthetic_data.py` - fresh people across hi/te/ta/mr/kn/en |
| 2 | Agent: "Who's due for a reminder?" -> tool called with `confirm=false` -> drafts in each person's language |
| 3 | Play the generated WAVs (the payoff moment) |
| 4 | Human: "Yes - but skip Mr. Rao" -> agent calls `confirm=true` with a scoped `event_ids` list |
| 5 | Rerun -> "No due events found" (idempotency) |
| 6 | Unset `SARVAM_API_KEY` -> per-event graceful failure, no crash |

## Data honesty

- Names, phone numbers, emails and events in `create_synthetic_data.py` are
  **fictional**. This repository has never contained personal data.
- The translation and voice audio are **really generated** by the Sarvam API
  during the demo.
- The *delivery channel* is demo-mode: the prototype records a dispatched
  reminder package (text + audio + timestamp). Production delivery is below.

## Production path (not built in the prototype)

- **Real phone calls:** Sarvam Voice Agents - outbound campaigns that dial
  contact lists and speak 11 languages natively, from ~INR 3/minute; rentable
  numbers, no separate telephony account.
- **WhatsApp / SMS:** the dispatch package (text + audio) plugs into the
  WhatsApp Business API or any SMS gateway without touching the workflow.
- The composed tool does not change: only the final delivery leg does.

## Repository history

Two commits tell the story:

1. `2b57543` - the original notification-to-call system (Django + Vonage
   SMS + IVR voice webhooks), personal data scrubbed.
2. The Sarvam swap - the same app, Vonage removed, one service module and one
   dispatch module added. `git diff` between them is the entire change; the
   Django models, admin, templates and project shell are untouched.

## Acknowledgements

- [Sarvam AI](https://sarvam.ai) - Translate (`mayura:v1`) and Bulbul
  text-to-speech for Indian languages.
- [Synapse CLI](https://github.com/2ndbrainlabs-ai/synapse-cli) - turns
  existing code into MCP servers without a rewrite.
