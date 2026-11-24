# Bolo - Multilingual Voice Reminder Copilot

**2nd Brain Labs x Geek Room Vibe-a-thon - Track 4 (Open Innovation)**

Bolo turns an existing Django notification system into an agent-operated copilot
that reminds people about upcoming events **in their own language, as a voice
message** — and closes the loop: every dispatched reminder carries a
**press-1 / press-2 confirmation** that flows back into the system.

A staff member asks an agent: *"who's due for a reminder?"* — the agent lists
the drafts, plays the generated Hindi / Telugu / Tamil / Marathi audio, waits
for a confirmation, dispatches, never double-sends, and later answers
*"who confirmed? who's still silent?"* from the reply data. A web dashboard
shows the same truth: every reminder, the transcript of what was said in each
language, the audio, and the client's reply.

The existing application was not rewritten. Synapse (Custom mode) composed its
internal workflow functions into one MCP tool - `dispatch_due_reminders` - in
minutes. That tool plus five hand-added siblings are the only ways the agent
touches the system.

---

## The problem

Small Indian businesses - clinics, salons, coaching centres, NBFC collections
teams - lose money to no-shows and missed follow-ups. Two gaps make it worse:

1. **Language.** English reminders get ignored; vernacular ones get answered.
2. **Operability.** Reminder tools need trained staff clicking through admin
   screens; the people who know who needs reminding are rarely those people.

Bolo closes both: every person carries a `preferred_language`, and the whole
workflow is a conversation (or one dashboard, or one cron call - your pick).

## What's in the box

| Surface | What it does |
|---|---|
| **Dashboard** (`/notifier/dashboard/`) | The dispatch table: person, event, status, **transcript in the client's language**, inline audio player, reply badge. Preview / dispatch per row or all-at-once; unsent drafts greyed; cancelled rows marked. |
| **Live dispatch feed** | WebSocket progress events while a batch runs (Django Channels, in-memory layer; import-guarded so the workflow works without it). |
| **Client reply page** (`/notifier/respond/<id>/`) | The press-1/press-2 loop as a page: plays the audio, two big buttons, latest reply wins. Same endpoint a real IVR or WhatsApp webhook posts into. |
| **Analytics** (`/notifier/analytics/`) | Response rate per language - the actual proof that vernacular reminders get answered. |
| **MCP tools** | `dispatch_due_reminders`, `add_reminder`, `reschedule_event`, `cancel_reminder`, `list_responses`, `reminder_digest`. |
| **Management commands** | `send_notifications` (manual), `auto_dispatch` (cron-friendly; `--loop-minutes N` to keep running). |
| **Reply webhooks** | `POST /notifier/webhook/response/` (IVR / bridge digits), `POST /notifier/webhook/call/` (Sarvam call outcomes) and `GET+POST /notifier/webhook/whatsapp/` (Meta button and text replies) — token-gated via `BOLO_WEBHOOK_TOKEN`, CSRF-exempt, disabled (403) until the token is set. |
| **DevOps** | `Dockerfile` + `docker-compose.yml` (migrations + first-boot demo seed on start, media volume, auto-restart), GitHub Actions CI (system check, migration check, full test suite — runs offline, no API key). |
| **Test suite** | 93 tests, Sarvam mocked - `python manage.py test notifier` runs offline in ~4s. |

The reminder lifecycle knows about quiet hours (per person, wraps midnight),
a retry ladder (no reply after N hours → one more attempt, capped),
cancellation (cancelled events are never sent) and **recurring events** —
dispatching a daily/weekly/monthly reminder automatically schedules its next
occurrence, idempotently, chained to the root event. Dispatch routes each package by the person's **channel preference** (WhatsApp /
voice call / SMS / demo page) through `notifier/services/delivery.py`; voice
calls (Sarvam Voice Agents instant outbound), SMS (MSG91 + respond link) and
WhatsApp (Meta Cloud API + Confirm / Decline buttons) are implemented and
env-gated, and a handler failure can never break a dispatch. Phone
numbers normalize to E.164 on save. Translation and TTS
are deduped by an in-process cache, and every Sarvam call retries with bounded
exponential backoff.

## How it works

The core is still one composable workflow
(`notifier/services/reminder_dispatch.py`):

1. **Find** - events whose reminder lead time has arrived, not dispatched, not
   cancelled; plus dispatched reminders that earned a retry (no reply, window
   elapsed, attempts left)
2. **Hold** - anything inside its person's quiet hours is reported as held,
   no API calls spent, nothing sent
3. **Personalize** - render each event's `message_template` (safe against odd
   templates)
4. **Translate** - colloquial translation via Sarvam's `mayura:v1`
   (`modern-colloquial` mode), 11 languages
5. **Voice** - Bulbul text-to-speech, saved as WAV under `media_root/reminders/`
6. **Dispatch & log** - only on confirmation: timestamps, audio path **and the
   transcript actually spoken** are persisted; concurrency-safe (row lock +
   re-check, so two parallel runs can never double-send)

`notifier/services/sarvam_service.py` is failure-safe end to end: a missing key,
an unreachable API or exhausted retries log the error and return `None`, so one
bad event reports a per-event failure instead of breaking the batch.

## The agent layer

Six MCP tools on one FastMCP server (`dispatch_due_reminders_server.py`,
connected via `opencode.jsonc`):

```
dispatch_due_reminders(confirm=false, event_ids=null)  # preview / dispatch
add_reminder(person_name, phone_number, what, when, ...)  # conversational intake
reschedule_event(event_id, when)   # resets send state for the new slot
cancel_reminder(event_id)          # never sent again
list_responses(status=null)        # confirmed / declined / pending
reminder_digest()                 # morning round: due today, replies, retries
```

- `confirm=false` **previews**: drafts + audio for every due event, nothing
  persisted. The agent structurally cannot send anything without a human yes.
- `add_reminder` drafts a warm message template with an LLM (Token Factory's
  OpenAI-compatible `/v2` endpoint, same SARVAM_API_KEY) when none is given -
  and falls back to a sensible default template if the LLM is unavailable.
- Because `confirm` gates every side effect, the safety story is in the tool
  contract itself, not in prompt instructions.

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env             # then add your SARVAM_API_KEY
python manage.py migrate
python create_synthetic_data.py    # 6 fictional people, 8 events, 6 due
python manage.py test notifier     # 93 tests, offline, no API key needed
python manage.py runserver         # open http://127.0.0.1:8000/notifier/dashboard/
```

Or with Docker (image runs migrations on start):

```bash
echo SARVAM_API_KEY=... > .env
docker compose up                  # dashboard on http://127.0.0.1:8000/notifier/dashboard/
```

From the dashboard: **Preview** a row to hear the generated audio, **Send now**
to dispatch, then open the **client page** on that row and press 1 or 2 to
watch the reply land. The same workflow without a browser or an agent:

```bash
python manage.py send_notifications            # preview (no changes)
python manage.py send_notifications --confirm  # actually dispatch
python manage.py auto_dispatch --confirm --loop-minutes 30   # cron in one process
```

## Build the MCP server with Synapse

```bash
npm install -g @2ndbrainlabs-ai/synapse-cli     # needs Node.js 20+

# Local build (your own model key, no Synapse account, no quota).
# One Sarvam key covers the codegen too, via Token Factory's
# OpenAI-compatible /v2 chat endpoint (it accepts Bearer auth):
set SYNAPSE_LLM_API_KEY=<same key as SARVAM_API_KEY>
synapse model set custom --base-url https://api.sarvam.ai/v2 --model sarvam-105b --global
synapse build --local --custom --query "Compose a tool named dispatch_due_reminders that finds due Events, personalizes each message_template, translates to each Person's preferred_language via Sarvam, generates speech audio with Sarvam Bulbul, and only when confirm=true records dispatch timestamps and skips already-dispatched events. Use notifier.services.reminder_dispatch and notifier.services.sarvam_service."
```

If the generated tool plan misses the schema on the custom path, first try
`--no-tool-choice` (Synapse then coerces JSON output instead), then pin a
different Token Factory model (`glm5.3`, `deepseekv4.1-flash-1017`), and only
then fall back to a direct `ANTHROPIC_API_KEY`.

The generated server is thin and ours to edit (per Synapse): the other five
tools were hand-added on the same scaffold. `opencode.jsonc` connects it —
open the project in OpenCode and the `bolo` server appears
(verify with `opencode mcp list`). The committed config points at the Linux
deployment's venv; running the agent from a Windows checkout means swapping
one line to `[".venv/Scripts/python.exe", "dispatch_due_reminders_server.py"]`
(both variants are commented in the file). `SARVAM_API_KEY` is read from `.env`
when the server starts (via `load_dotenv()` in settings), so it never appears
in any config. The same server file plugs into Claude Desktop or Cursor.

## Demo script (~4 minutes)

| Beat | What happens |
|---|---|
| 1 | `python create_synthetic_data.py` - fresh people across hi/te/ta/mr/kn/en, with channel preferences and quiet hours |
| 2 | Agent: "Who's due for a reminder?" -> `confirm=false` -> drafts in each person's language |
| 3 | Play the generated WAVs from the dashboard's inline players (the payoff moment) |
| 4 | Human: "Yes - but skip Mr. Rao" -> `confirm=true` with a scoped `event_ids` list |
| 5 | Dashboard rows flip to **sent** with the transcript; open one client page and press 1 -> the row flips to **confirmed** |
| 6 | Agent: "Who's confirmed?" -> `list_responses()`; Analytics tab shows response rate per language |
| 7 | Rerun -> "No due events found" (idempotency); unset `SARVAM_API_KEY` -> per-event graceful failure, no crash |

## Data honesty

- Names, phone numbers, emails and events in `create_synthetic_data.py` are
  **fictional**. This repository has never contained personal data.
- Translation, voice audio and (when enabled) LLM-drafted templates are
  **really generated** by the Sarvam API during the demo.
- The *delivery channel* is demo-mode: the prototype records a dispatched
  reminder package (text + audio + timestamp) and the client reply is captured
  on the demo response page — through the exact endpoint an IVR or WhatsApp
  webhook would POST into. Production delivery is below.

## Production path

All three legs are **implemented and env-gated** — they activate the
moment their credentials appear in `.env`, and fall back to the recorded demo
package otherwise:

- **Real phone calls (implemented):** Sarvam Voice Agents (Samvaad) instant
  outbound API — `notifier/services/call_service.py` places one AI call per
  dispatched reminder. The agent flow declares an input variable
  `reminder_text` (Variables tab); the payload sends the already-translated
  reminder — in the person's own language, with their name — as that
  variable's value, and the flow's instruction relays it and asks whether
  the client can make it. The answer is extracted after the call into the
  `client_reply` output variable (Enum: yes / no / unclear). Outcomes POST
  to `/notifier/webhook/call/`: a yes/no answer flows into `record_response`
  like any other channel; unclear or unanswered calls stay PENDING for the
  retry ladder. Configure the agent, connection and number in the Voice
  Agents workspace (see `.env.example` for the recipe). Numbers rentable
  (about INR 59 per 30 days plus INR 0.20 per 30-second pulse, unanswered
  calls free), no separate telephony account.
- **SMS (implemented):** MSG91's v5 flow API —
  `notifier/services/sms_service.py` texts the vernacular reminder plus a link
  to the client reply page (audio + press-1/press-2), so the loop closes over
  plain SMS with no app required. Needs a DLT-registered template (TRAI);
  VAR1 carries the text, VAR2 the link.
- **WhatsApp (implemented):** Meta's WhatsApp Cloud API —
  `notifier/services/whatsapp_service.py` sends the vernacular reminder plus
  the respond link as one interactive message with Confirm / Decline buttons
  whose ids carry the event id. Replies POST to
  `/notifier/webhook/whatsapp/`: the portal handshake uses `BOLO_WEBHOOK_TOKEN`
  as the verify token, `WHATSAPP_APP_SECRET` verifies `X-Hub-Signature-256`
  on callbacks, button replies correlate by their id and plain-text yes/no by
  sender phone, all funneling into `record_response` like any other channel.
  Business-initiated freeform messages deliver inside the 24-hour
  customer-service window or to test numbers; production campaigns use a
  pre-approved utility template (the WhatsApp analogue of the DLT note).
  Prefer an aggregator? Swap the sender module for Twilio or Gupshup; the
  dispatch package and webhook stay unchanged.
- The composed tools do not change: only the final delivery leg does.

## Repository history

The history tells the story in stages:

1. The original notification-to-call system (Django + Vonage SMS + IVR voice
   webhooks), personal data scrubbed.
2. The Sarvam swap - the same app, Vonage removed, one service module and one
   dispatch module added.
3. The copilot expansion - response capture (press-1/press-2), the dashboard
   with transcripts and audio, five more agent tools, quiet hours, retry
   ladder, cancellation, recurring-event spawning, channel-preference
   delivery routing, the reply webhook, analytics, the live feed, the test
   suite, Docker and CI.

## Acknowledgements

- [Sarvam AI](https://sarvam.ai) - Translate (`mayura:v1`), Bulbul
  text-to-speech, and Token Factory chat for LLM-drafted templates.
- [Synapse CLI](https://github.com/2ndbrainlabs-ai/synapse-cli) - turns
  existing code into MCP servers without a rewrite.
- [Django Channels](https://channels.readthedocs.io) - the live dispatch feed.
