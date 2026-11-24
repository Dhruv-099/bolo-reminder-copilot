# AGENTS.md

Bolo: a Django app (`notifier`) that drafts, translates and dispatches
multilingual voice reminders, driven by agent tools. The dispatch workflow
lives in `notifier/services/reminder_dispatch.py`; channel senders in
`notifier/services/` (call, sms, whatsapp, delivery); agent tools in
`notifier/services/agent_tools.py`, exposed by the FastMCP server
`dispatch_due_reminders_server.py` (wired via `opencode.jsonc`).

## Commands

- Tests (offline, ~4 s, no API key): `python manage.py test notifier`
- Single test: `python manage.py test notifier.tests.CallServiceTests.test_payload_shape`
- Dev server: `python manage.py runserver` → dashboard `/notifier/dashboard/`
- Seed demo data (local dev): `python create_synthetic_data.py`. The docker
  stack instead runs `python manage.py seed_demo` on first boot.
- Dispatch by hand: `python manage.py send_notifications` (preview only),
  `--confirm` to really send; `python manage.py auto_dispatch --confirm
  --loop-minutes 30` keeps running like a cron.
- Docker: `docker compose up -d --build`. Code changes need the rebuild;
  `.env`-only changes need just `up -d` (recreate reloads `env_file`).
  `./db.sqlite3` is bind-mounted, so demo data survives recreation.

## Environment lifecycle (the biggest gotcha)

`.env` is never tracked (only `.env.example`) and is read via `load_dotenv()`
at process start — so every change needs a restart: `docker compose up -d`
for the container, a fresh session for the MCP server (long-lived servers
hold stale env). `SARVAM_API_KEY` powers translate + TTS + LLM drafts. Each
delivery leg is env-gated (`SARVAM_VOICE_*`, `MSG91_*`, `WHATSAPP_*`) and
must fall back to the recorded demo package when unset — never raise.
`BOLO_WEBHOOK_TOKEN` gates all three reply webhooks (403 until set);
`BOLO_PUBLIC_BASE_URL` builds outbound links and webhook URLs, so real
provider callbacks need it publicly reachable (cloudflared quick tunnel —
re-pin `.env` whenever the tunnel restarts).

## Rules that are easy to break

1. **Everything flows through the workflow.** Sends and replies go through
   `reminder_dispatch` + `delivery` handlers or the MCP tools that wrap
   them. Hand-rolled provider calls bypass dispatch bookkeeping, and
   `record_response` rejects events without `voice_dispatched_at`
   (`not_dispatched`), so webhook replies 400 silently.
2. `confirm=false` previews and persists nothing. The confirm gate is the
   safety story — keep it in the tool contract, not in prompts.
3. Providers validate strictly: Sarvam's outbound API 422s on any
   `agent_variables` name the flow has not declared on its Variables tab;
   `call_service` sends only what `SARVAM_VOICE_AGENT_VARS` lists (default
   `reminder_text`). Capture provider error bodies in failures — they name
   the offending field.
4. `TIME_ZONE=Asia/Kolkata`, `USE_TZ=True`. Never `.strftime()` a raw aware
   datetime (renders UTC) — wrap with `timezone.localtime(...)`. Django's
   `|date:` filter localizes correctly on its own.
5. The voice-agent recipe (Sarvam portal): flow declares `reminder_text`
   (input) and `client_reply` (Output, Enum yes/no/unclear). unclear or
   unanswered → no digit → PENDING → the retry ladder owns it. The
   `initial_bot_message` override is unreliable on committed flows; the
   input-vars mechanism is the supported one.
6. Webhook auth shapes differ: Sarvam's inline `webhook_config` carries no
   headers (token rides in the query string); Meta uses `hub.verify_token`
   plus `X-Hub-Signature-256` (verified when `WHATSAPP_APP_SECRET` is set);
   the generic response webhook takes Bearer / X-Api-Key / `token` param.
7. `Person.save` normalizes phones to E.164 (bare 10-digit → `+91`).
8. In `add_reminder`, `what` is required and becomes the spoken details
   (`details or what`); `details` is optional refinement.

## Tests

- Fully offline: `SarvamMockMixin` patches `translate_message` /
  `generate_voice_audio` and clears the in-process dedupe caches;
  per-service tests mock `requests.post`. Never hit real APIs in tests.
- Never hardcode future dates — they rot (a due-ness assertion once failed
  this way). Compute from `timezone.now()`.
- CI (GitHub Actions) runs system + migration checks and the full suite
  with no API key.

## Remotes

Two: `origin` (git.bobalhouse.co.in) and `github` (the public mirror the
hackathon submission links to). Push both.
