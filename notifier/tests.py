"""Bolo test suite: templates, dispatch workflow, responses, agent tools, views.

Sarvam is mocked everywhere, so the suite runs offline and fast. Run with:

    python manage.py test notifier
"""

import hashlib
import hmac
import json
import os
import requests
from datetime import datetime, time, timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from notifier.models import Event, Person
from notifier.services import agent_tools, call_service, delivery, sarvam_service, sms_service, whatsapp_service
from notifier.services.reminder_dispatch import (
    dispatch_due_reminders,
    find_due_events,
    find_retry_due_events,
    is_quiet_now,
    personalize_message,
    spawn_next_occurrence,
)
from notifier.services.responses import record_response


def make_person(name="Test Person", phone="+919000000099", language="hi-IN", **kwargs):
    return Person.objects.create(
        name=name, phone_number=phone, preferred_language=language, **kwargs
    )


def make_event(person, minutes_from_now=30, lead=120, **kwargs):
    defaults = dict(
        event_type=Event.EventType.REMINDER,
        event_datetime=timezone.now() + timedelta(minutes=minutes_from_now),
        message_template="Hi {name}, your {event_type_display} '{details}' is {time_left}.",
        details="Test detail",
        notification_lead_time_minutes=lead,
    )
    defaults.update(kwargs)
    return Event.objects.create(person=person, **defaults)


def mocked_sarvam(translate="नमस्ते", audio="reminders/test.wav"):
    """Context manager mocking both Sarvam calls the dispatch workflow makes."""
    return mock.patch.multiple(
        sarvam_service,
        translate_message=mock.DEFAULT if translate is None else mock.DEFAULT,
        generate_voice_audio=mock.DEFAULT,
    ), translate, audio


class SarvamMockMixin:
    """Patches the two Sarvam calls for the duration of a test."""

    def setUp(self):
        super().setUp()
        sarvam_service.clear_service_caches()
        patcher_t = mock.patch.object(sarvam_service, "translate_message", return_value="नमस्ते")
        patcher_a = mock.patch.object(
            sarvam_service, "generate_voice_audio", return_value="reminders/test.wav"
        )
        self.mock_translate = patcher_t.start()
        self.mock_audio = patcher_a.start()
        self.addCleanup(patcher_t.stop)
        self.addCleanup(patcher_a.stop)


class PersonalizeMessageTests(TestCase):
    def test_replaces_known_placeholders(self):
        person = make_person(name="Rahul Verma")
        event = make_event(person, details="Project Alpha Sync")
        text = personalize_message(event)
        self.assertIn("Rahul", text)
        self.assertIn("Project Alpha Sync", text)
        self.assertIn("generic reminder", text.lower())

    def test_unknown_placeholder_renders_safely(self):
        event = make_event(make_person(), message_template="Hi {name}, {unknown_tag} at {time}.")
        text = personalize_message(event)
        self.assertIn("Hi Test", text)
        self.assertNotIn("{", text)

    def test_stray_braces_are_dropped(self):
        event = make_event(make_person(), message_template="Hi {name} {{literal}} bye.")
        text = personalize_message(event)
        self.assertNotIn("{{literal}}", text)


class DueSelectionTests(TestCase):
    def test_not_due_before_lead_time(self):
        # Event is 5 hours away; a 60-minute lead time means it fires in ~4 hours.
        event = make_event(make_person(), minutes_from_now=300, lead=60)
        self.assertFalse(event.is_notification_due)

    def test_due_at_lead_time(self):
        event = make_event(make_person(), minutes_from_now=60, lead=120)
        self.assertTrue(event.is_notification_due)

    def test_dispatched_and_cancelled_excluded(self):
        person = make_person()
        dispatched = make_event(person, voice_dispatched_at=timezone.now())
        cancelled = make_event(person, cancelled_at=timezone.now())
        fresh = make_event(person)
        due_ids = {event.id for event in find_due_events()}
        self.assertIn(fresh.id, due_ids)
        self.assertNotIn(dispatched.id, due_ids)
        self.assertNotIn(cancelled.id, due_ids)

    def test_retry_ladder(self):
        person = make_person()
        seven_hours_ago = timezone.now() - timedelta(hours=7)
        base = dict(
            voice_dispatched_at=seven_hours_ago,
            response_value=Event.ResponseValue.PENDING,
            dispatch_attempts=1,
        )
        retryable = make_event(person, **base)
        maxed = make_event(person, **{**base, "dispatch_attempts": 2})
        answered = make_event(person, **{**base, "response_value": Event.ResponseValue.CONFIRMED})
        too_soon = make_event(person, **{**base, "voice_dispatched_at": timezone.now() - timedelta(hours=1)})
        due = {event.id for event in find_retry_due_events()}
        self.assertIn(retryable.id, due)
        self.assertNotIn(maxed.id, due)
        self.assertNotIn(answered.id, due)
        self.assertNotIn(too_soon.id, due)

    def test_quiet_hours_wrap_midnight(self):
        person = make_person(quiet_hours_start=time(22, 0), quiet_hours_end=time(8, 0))
        self.assertTrue(is_quiet_now(person, at=datetime(2026, 1, 1, 23, 30)))
        self.assertTrue(is_quiet_now(person, at=datetime(2026, 1, 1, 6, 0)))
        self.assertFalse(is_quiet_now(person, at=datetime(2026, 1, 1, 12, 0)))
        self.assertFalse(is_quiet_now(make_person()))  # no quiet hours configured


class DispatchWorkflowTests(SarvamMockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.person = make_person(language="hi-IN")
        self.event = make_event(self.person)

    def test_confirm_persists_transcripts_and_attempts(self):
        report = dispatch_due_reminders(confirm=True)
        self.event.refresh_from_db()
        self.assertEqual(report["dispatched"], 1)
        self.assertEqual(report["results"][0]["status"], "dispatched")
        self.assertIsNotNone(self.event.voice_dispatched_at)
        self.assertEqual(self.event.voice_transcript_local, "नमस्ते")
        self.assertIn("Hi Test", self.event.voice_transcript_english)
        self.assertEqual(self.event.dispatch_attempts, 1)
        self.assertEqual(self.event.voice_audio_path, "reminders/test.wav")

    def test_second_run_is_idempotent(self):
        first = dispatch_due_reminders(confirm=True)
        second = dispatch_due_reminders(confirm=True)
        self.assertEqual(first["dispatched"], 1)
        self.assertEqual(second["due_found"], 0)
        self.assertEqual(second["message"], "No due events found.")

    def test_preview_changes_nothing(self):
        report = dispatch_due_reminders(confirm=False)
        self.event.refresh_from_db()
        self.assertEqual(report["results"][0]["status"], "ready")
        self.assertIsNone(self.event.voice_dispatched_at)
        self.assertEqual(self.event.dispatch_attempts, 0)
        self.assertEqual(report["due_found"], 1)
        self.assertEqual(report["dispatched"], 0)

    def test_concurrent_dispatch_marks_skipped(self):
        # Simulate the race the way it actually happens: another run writes
        # the dispatch timestamp to the DB *after* our snapshot was taken
        # (so the in-memory event still sees voice_dispatched_at=None).
        Event.objects.filter(pk=self.event.pk).update(voice_dispatched_at=timezone.now())
        with mock.patch(
            "notifier.services.reminder_dispatch.find_due_events", return_value=[self.event]
        ):
            report = dispatch_due_reminders(confirm=True)
        self.event.refresh_from_db()
        self.assertEqual(report["results"][0]["status"], "skipped")
        self.assertEqual(report["results"][0]["error"], "already_dispatched_concurrently")
        self.assertEqual(self.event.dispatch_attempts, 0, "skipped events must stay untouched")

    def test_quiet_hours_hold(self):
        self.person.quiet_hours_start = time(0, 0)
        self.person.quiet_hours_end = time(23, 59)
        self.person.save()
        report = dispatch_due_reminders(confirm=True)
        self.event.refresh_from_db()
        self.assertEqual(report["results"][0]["status"], "held_quiet_hours")
        self.assertEqual(report["held_quiet_hours"], 1)
        self.assertIsNone(self.event.voice_dispatched_at)
        self.mock_translate.assert_not_called()
        self.mock_audio.assert_not_called()

    def test_translation_failure_reported_not_raised(self):
        self.mock_translate.return_value = None
        report = dispatch_due_reminders(confirm=False)
        item = report["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"], "translation_failed")
        self.event.refresh_from_db()
        self.assertIsNone(self.event.voice_dispatched_at)

    def test_tts_failure_reported_not_raised(self):
        self.mock_audio.return_value = None
        report = dispatch_due_reminders(confirm=False)
        item = report["results"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"], "audio_failed")

    def test_scoped_event_ids(self):
        other = make_event(make_person(phone="+919000000098"))
        report = dispatch_due_reminders(confirm=True, event_ids=[self.event.id])
        other.refresh_from_db()
        self.assertEqual(report["due_found"], 1)
        self.assertIsNone(other.voice_dispatched_at)


class RetryAfterTests(SarvamMockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.person = make_person(language="hi-IN")
        self.event = make_event(self.person)

    def test_retry_eligible_event_is_included(self):
        self.event.voice_dispatched_at = timezone.now() - timedelta(hours=7)
        self.event.response_value = Event.ResponseValue.PENDING
        self.event.dispatch_attempts = 1
        self.event.save()
        report = dispatch_due_reminders(confirm=True, event_ids=[self.event.id])
        self.event.refresh_from_db()
        self.assertEqual(report["results"][0]["status"], "dispatched")
        self.assertTrue(report["results"][0]["retry"])
        self.assertEqual(self.event.dispatch_attempts, 2, "retry must bump the attempt count")


class ResponseTests(TestCase):
    def setUp(self):
        self.person = make_person()
        self.event = make_event(self.person, voice_dispatched_at=timezone.now())

    def test_press_one_confirms(self):
        result = record_response(self.event.id, "1")
        self.event.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)
        self.assertIsNotNone(self.event.response_received_at)
        self.assertEqual(self.event.response_channel, "demo_page")

    def test_press_two_declines(self):
        result = record_response(self.event.id, "2")
        self.event.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(self.event.response_value, Event.ResponseValue.DECLINED)

    def test_latest_reply_wins(self):
        record_response(self.event.id, "2")
        record_response(self.event.id, "1")
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)

    def test_invalid_digit_rejected(self):
        result = record_response(self.event.id, "9")
        self.event.refresh_from_db()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_digit")
        self.assertEqual(self.event.response_value, Event.ResponseValue.PENDING)

    def test_unsent_event_refused(self):
        unsent = make_event(self.person)
        result = record_response(unsent.id, "1")
        unsent.refresh_from_db()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_dispatched")
        self.assertEqual(unsent.response_value, Event.ResponseValue.PENDING)

    def test_unknown_event(self):
        result = record_response(999999, "1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "event_not_found")


class PhoneNormalizationTests(TestCase):
    def test_bare_ten_digits_get_india_code(self):
        person = Person(name="P", phone_number="9810690521")
        person.save()
        self.assertEqual(person.phone_number, "+919810690521")

    def test_twelve_digit_indian_number(self):
        person = Person(name="P", phone_number="919000000002")
        person.save()
        self.assertEqual(person.phone_number, "+919000000002")

    def test_international_passthrough(self):
        person = Person(name="P", phone_number="+441234567890")
        person.save()
        self.assertEqual(person.phone_number, "+441234567890")

    def test_spaces_and_dashes_stripped(self):
        person = Person(name="P", phone_number="+91 98100 00003")
        person.save()
        self.assertEqual(person.phone_number, "+919810000003")


class AgentToolsTests(TestCase):
    def setUp(self):
        # Never let the LLM draft a real template during tests.
        patcher = mock.patch.object(
            agent_tools.llm_draft, "draft_message_template", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_reminder_creates_person_and_event(self):
        # A future slot computed at runtime, so the lead-time assertion below
        # never rots the way a hardcoded date would.
        future = (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        result = agent_tools.add_reminder(
            person_name="Dhruv Test", phone_number="919000009101", what="Dental appointment",
            when=future, preferred_language="hi-IN", event_type="MEETING",
            message_template="Hi {name}, your {event_type_display} '{details}' is {time_left}.",
        )
        self.assertTrue(result["ok"], result)
        event = Event.objects.get(pk=result["event_id"])
        self.assertEqual(event.person.phone_number, "+919000009101")
        self.assertEqual(event.person.preferred_language, "hi-IN")
        self.assertFalse(event.is_notification_due, "lead time not reached yet")
        self.assertFalse(result["template_drafted_by_llm"])

    def test_add_reminder_without_template_uses_llm_or_default(self):
        result = agent_tools.add_reminder(
            person_name="No Key", phone_number="+919000009102", what="Voice test",
            when="2026-11-22 16:00",
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["template_drafted_by_llm"])
        self.assertEqual(result["message_template"], agent_tools.DEFAULT_TEMPLATE)

    def test_add_reminder_reuses_person_by_phone(self):
        agent_tools.add_reminder(
            person_name="First Name", phone_number="+919000009103", what="A",
            when="2026-11-22 16:00", message_template="Hi {name}, {details}.",
        )
        result = agent_tools.add_reminder(
            person_name="Renamed", phone_number="+919000009103", what="B",
            when="2026-11-23 10:00", message_template="Hi {name}, {details}.",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["person_created"])
        person = Person.objects.get(phone_number="+919000009103")
        self.assertEqual(person.name, "Renamed")

    def test_add_reminder_validation(self):
        self.assertEqual(
            agent_tools.add_reminder(person_name="", phone_number="+910000000000", what="x", when="2026-11-22 16:00")["error"],
            "missing_person_name",
        )
        self.assertEqual(
            agent_tools.add_reminder(person_name="x", phone_number="12345", what="y", when="2026-11-22 16:00")["error"],
            "invalid_phone",
        )
        self.assertEqual(
            agent_tools.add_reminder(person_name="x", phone_number="+910000000000", what="y", when="tomorrowish")["error"],
            "invalid_when",
        )
        self.assertEqual(
            agent_tools.add_reminder(person_name="x", phone_number="+910000000000", what="y", when="2026-11-22 16:00", event_type="BANANA")["error"],
            "invalid_event_type",
        )

    def test_parse_when_date_only_defaults_to_nine_am(self):
        parsed, error = agent_tools._parse_when("2026-11-22")
        self.assertIsNone(error)
        self.assertEqual(parsed.hour, 9)

    def test_reschedule_resets_dispatch_state(self):
        event = make_event(make_person(phone="+919000009104"))
        event.voice_dispatched_at = timezone.now()
        event.response_value = Event.ResponseValue.CONFIRMED
        event.save()
        result = agent_tools.reschedule_event(event.id, "2026-11-24 09:15")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["send_state_reset"])
        event.refresh_from_db()
        self.assertIsNone(event.voice_dispatched_at)
        self.assertEqual(event.response_value, Event.ResponseValue.PENDING)
        self.assertEqual(timezone.localtime(event.event_datetime).hour, 9)

    def test_cancel_and_double_cancel(self):
        event = make_event(make_person(phone="+919000009105"))
        first = agent_tools.cancel_reminder(event.id, reason="smoke")
        self.assertTrue(first["ok"])
        event.refresh_from_db()
        self.assertFalse(event.is_notification_due)
        second = agent_tools.cancel_reminder(event.id)
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "already_cancelled")

    def test_list_responses_and_digest(self):
        person = make_person(phone="+919000009106")
        confirmed = make_event(person, voice_dispatched_at=timezone.now(), response_value=Event.ResponseValue.CONFIRMED)
        pending = make_event(person, voice_dispatched_at=timezone.now())
        responses = agent_tools.list_responses()
        self.assertEqual(responses["confirmed"], 1)
        self.assertEqual(responses["awaiting_reply"], 1)
        scoped = agent_tools.list_responses("confirmed")
        self.assertTrue(all(row["response"] == "CONFIRMED" for row in scoped["results"]))

        digest = agent_tools.reminder_digest()
        self.assertTrue(digest["ok"])
        self.assertIn("due_today", digest)
        self.assertIn("retry_due", digest)

    def test_missing_event_errors(self):
        self.assertEqual(agent_tools.cancel_reminder(999999)["error"], "event_not_found")
        self.assertEqual(agent_tools.reschedule_event(999999, "2026-11-22 10:00")["error"], "event_not_found")


class ViewTests(SarvamMockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.person = make_person()
        self.dispatched = make_event(
            self.person,
            voice_dispatched_at=timezone.now(),
            voice_audio_path="reminders/x.wav",
            voice_transcript_english="Hi Test, this is a reminder.",
            voice_transcript_local="नमस्ते, यह एक अनुस्मारक है।",
        )
        self.due = make_event(self.person)

    def test_dashboard_renders(self):
        response = self.client.get("/notifier/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.person.name)

    def test_respond_page_shows_transcript_and_buttons(self):
        response = self.client.get(f"/notifier/respond/{self.dispatched.id}/")
        self.assertContains(response, "नमस्ते")
        self.assertContains(response, "press 1")

    def test_respond_post_records(self):
        response = self.client.post(f"/notifier/respond/{self.dispatched.id}/", {"digit": "1"})
        self.assertEqual(response.status_code, 302)
        self.dispatched.refresh_from_db()
        self.assertEqual(self.dispatched.response_value, Event.ResponseValue.CONFIRMED)

    def test_respond_refuses_unsent_event(self):
        response = self.client.post(f"/notifier/respond/{self.due.id}/", {"digit": "1"})
        self.assertEqual(response.status_code, 302)
        self.due.refresh_from_db()
        self.assertEqual(self.due.response_value, Event.ResponseValue.PENDING)

    def test_preview_of_dispatched_event_redirects_without_sarvam_calls(self):
        response = self.client.post(f"/notifier/event/{self.dispatched.id}/preview/")
        self.assertEqual(response.status_code, 302)
        self.mock_translate.assert_not_called()
        self.mock_audio.assert_not_called()

    def test_preview_renders_draft(self):
        fake_report = {
            "event_id": self.due.id,
            "person": self.person.name,
            "event_type": "Generic Reminder",
            "event_datetime": timezone.localtime(self.due.event_datetime).isoformat(),
            "language": "hi-IN",
            "text_english": "Hi Test, your generic reminder 'Test detail' is in 30 minutes.",
            "text_local": "नमस्ते",
            "audio_path": "reminders/test.wav",
            "audio_file": "C:\\fake\\test.wav",
            "status": "ready",
            "error": None,
        }
        with mock.patch("notifier.views.prepare_reminder", return_value=fake_report):
            response = self.client.post(f"/notifier/event/{self.due.id}/preview/")
        self.assertContains(response, "नमस्ते")

    def test_event_dispatch_uses_workflow_and_redirects(self):
        fake_report = {
            "confirmed": True, "due_found": 1, "dispatched": 1, "failed": 0,
            "skipped": 0, "held_quiet_hours": 0,
            "results": [{"status": "dispatched", "error": None}], "message": "done",
        }
        with mock.patch("notifier.views.dispatch_due_reminders", return_value=fake_report) as workflow:
            response = self.client.post(f"/notifier/event/{self.due.id}/dispatch/")
        self.assertEqual(response.status_code, 302)
        workflow.assert_called_once_with(confirm=True, event_ids=[self.due.id])

    def test_analytics_renders(self):
        response = self.client.get("/notifier/analytics/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Response analytics")


class SarvamCacheTests(TestCase):
    def test_translate_uses_cache(self):
        sarvam_service.clear_service_caches()
        with mock.patch.object(sarvam_service, "_headers", return_value={"api-subscription-key": "k"}), \
             mock.patch.object(
                 sarvam_service, "_post_with_retries",
                 return_value={"translated_text": "नमस्ते"},
             ) as post:
            first = sarvam_service.translate_message("Hello", "hi-IN")
            second = sarvam_service.translate_message("Hello", "hi-IN")
        self.assertEqual(first, "नमस्ते")
        self.assertEqual(second, "नमस्ते")
        post.assert_called_once(), "second identical translation must come from the cache"

    def test_retries_exhausted_return_none(self):
        with mock.patch.object(sarvam_service, "_headers", return_value={"api-subscription-key": "k"}), \
             mock.patch(
                 "notifier.services.sarvam_service.requests.post",
                 side_effect=sarvam_service.requests.RequestException("boom"),
             ) as post:
            result = sarvam_service.translate_message("Hello", "hi-IN")
        self.assertIsNone(result)
        self.assertEqual(post.call_count, 3, "bounded retry: three attempts, then give up")


class RecurrenceTests(SarvamMockMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.person = make_person()

    def test_daily_dispatch_spawns_next_occurrence(self):
        event = make_event(self.person, recurrence=Event.Recurrence.DAILY)
        report = dispatch_due_reminders(confirm=True)
        item = report["results"][0]
        self.assertEqual(item["status"], "dispatched")
        spawned = Event.objects.get(pk=item["spawned_next_id"])
        self.assertEqual(spawned.recurrence_parent_id, event.id, "occurrences chain to the root event")
        self.assertEqual(spawned.event_datetime - event.event_datetime, timedelta(days=1))
        self.assertEqual(spawned.message_template, event.message_template)
        self.assertEqual(spawned.dispatch_attempts, 0)
        self.assertFalse(spawned.is_notification_due, "next occurrence must respect its own lead time")

    def test_spawn_is_idempotent(self):
        event = make_event(self.person, recurrence=Event.Recurrence.WEEKLY)
        first = spawn_next_occurrence(event)
        second = spawn_next_occurrence(event)
        self.assertIsNotNone(first)
        self.assertIsNone(second, "the same slot must never spawn twice")

    def test_monthly_clamps_month_end(self):
        jan31 = timezone.make_aware(datetime(2026, 1, 31, 16, 0), timezone.get_current_timezone())
        event = make_event(self.person, event_datetime=jan31, recurrence=Event.Recurrence.MONTHLY)
        spawned = spawn_next_occurrence(event)
        self.assertEqual(
            (spawned.event_datetime.year, spawned.event_datetime.month, spawned.event_datetime.day),
            (2026, 2, 28),
            "Jan 31 monthly must clamp to Feb 28",
        )

    def test_non_recurring_does_not_spawn(self):
        make_event(self.person)
        report = dispatch_due_reminders(confirm=True)
        self.assertNotIn("spawned_next_id", report["results"][0])
        self.assertEqual(Event.objects.count(), 1)


class DeliveryBranchTests(SarvamMockMixin, TestCase):
    def _dispatch_one(self, person):
        make_event(person)
        return dispatch_due_reminders(confirm=True)["results"][0]

    def test_demo_page_delivery_is_recorded(self):
        person = make_person(channel_preference=Person.ChannelPreference.DEMO_PAGE)
        delivery_info = self._dispatch_one(person)["delivery"]
        self.assertEqual(delivery_info["via"], "demo_page")
        self.assertEqual(delivery_info["mode"], "recorded")

    def test_whatsapp_send_failure_falls_back(self):
        person = make_person(
            phone="+919000000011", channel_preference=Person.ChannelPreference.WHATSAPP
        )
        with mock.patch.object(
            delivery.whatsapp_service,
            "send_reminder_message",
            return_value={"ok": False, "error": "not_configured"},
        ):
            delivery_info = self._dispatch_one(person)["delivery"]
        self.assertEqual(delivery_info["via"], "whatsapp")
        self.assertEqual(delivery_info["mode"], "recorded_fallback:whatsapp")

    def test_voice_call_and_sms_fall_back(self):
        for pref, via in (
            (Person.ChannelPreference.VOICE_CALL, "voice_call"),
            (Person.ChannelPreference.SMS, "sms"),
        ):
            person = make_person(
                phone=f"+91900000001{int(pref == Person.ChannelPreference.SMS)}",
                channel_preference=pref,
            )
            delivery_info = self._dispatch_one(person)["delivery"]
            self.assertEqual(delivery_info["via"], via)
            self.assertEqual(delivery_info["mode"], f"recorded_fallback:{via}")

    def test_handler_failure_never_breaks_dispatch(self):
        person = make_person(
            phone="+919000000013", channel_preference=Person.ChannelPreference.DEMO_PAGE
        )

        def boom(event, package):
            raise RuntimeError("channel exploded")

        with mock.patch.object(delivery, "CHANNEL_HANDLERS", {Person.ChannelPreference.DEMO_PAGE: boom}):
            item = self._dispatch_one(person)
        self.assertEqual(item["status"], "dispatched", "a channel failure must not undo the dispatch")
        self.assertEqual(item["delivery"]["mode"], "error")


class ResponseWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.person = make_person()
        self.event = make_event(self.person, voice_dispatched_at=timezone.now())
        self.env = mock.patch.dict(os.environ, {"BOLO_WEBHOOK_TOKEN": "sekrit-token"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def post_json(self, payload, **headers):
        return self.client.post(
            "/notifier/webhook/response/",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def test_bearer_token_records_reply(self):
        response = self.post_json(
            {"event_id": self.event.id, "digit": "1", "channel": "ivr"},
            HTTP_AUTHORIZATION="Bearer sekrit-token",
        )
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)
        self.assertEqual(self.event.response_channel, "ivr")

    def test_x_api_key_header(self):
        response = self.post_json(
            {"event_id": self.event.id, "digit": "2"},
            HTTP_X_API_KEY="sekrit-token",
        )
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.DECLINED)
        self.assertEqual(self.event.response_channel, "webhook", "channel defaults when not given")

    def test_wrong_token_is_unauthorized(self):
        response = self.post_json(
            {"event_id": self.event.id, "digit": "1"},
            HTTP_AUTHORIZATION="Bearer nope",
        )
        self.assertEqual(response.status_code, 401)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.PENDING)

    def test_disabled_without_env_token(self):
        os.environ.pop("BOLO_WEBHOOK_TOKEN", None)
        try:
            response = self.post_json({"event_id": self.event.id, "digit": "1"})
        finally:
            os.environ["BOLO_WEBHOOK_TOKEN"] = "sekrit-token"
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "webhook_disabled")

    def test_form_encoded_body_with_token_param(self):
        response = self.client.post(
            "/notifier/webhook/response/",
            data={"event_id": self.event.id, "digit": "1", "token": "sekrit-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)

    def test_invalid_digit_is_reported(self):
        response = self.post_json(
            {"event_id": self.event.id, "digit": "9"},
            HTTP_AUTHORIZATION="Bearer sekrit-token",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_digit")

    def test_missing_event_id_rejected(self):
        response = self.post_json({"digit": "1"}, HTTP_AUTHORIZATION="Bearer sekrit-token")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_event_id")


VOICE_ENV = {
    "SARVAM_VOICE_API_KEY": "vk",
    "SARVAM_VOICE_ORG_ID": "org",
    "SARVAM_VOICE_WORKSPACE_ID": "ws",
    "SARVAM_VOICE_APP_ID": "app",
    "SARVAM_VOICE_CONNECTION_ID": "conn",
    "SARVAM_VOICE_NUMBER": "+911111111111",
    "BOLO_WEBHOOK_TOKEN": "tok",
    "BOLO_PUBLIC_BASE_URL": "http://192.168.1.15:8000",
}


class SmsServiceTests(TestCase):
    def setUp(self):
        self.person = make_person(phone="+919000000021")
        self.event = make_event(self.person)
        self.package = {"text_local": "नमस्ते, यह अनुस्मारक है।", "text_english": "Hi, this is a reminder."}

    def test_not_configured(self):
        with mock.patch.object(sms_service, "_msg91_configured", return_value=False):
            result = sms_service.send_reminder_sms(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_configured")

    def test_payload_shape(self):
        with mock.patch.dict(os.environ, {"MSG91_AUTH_KEY": "key123", "MSG91_FLOW_ID": "flow9"}), \
             mock.patch("notifier.services.sms_service.requests.post") as post:
            post.return_value.json.return_value = {"type": "success"}
            result = sms_service.send_reminder_sms(self.event, self.package)
        self.assertTrue(result["ok"], result)
        payload = post.call_args.kwargs["json"]
        recipient = payload["recipients"][0]
        self.assertEqual(recipient["mobiles"], "919000000021", "leading plus must be stripped")
        self.assertEqual(recipient["VAR1"], "नमस्ते, यह अनुस्मारक है।")
        self.assertIn("/notifier/respond/", recipient["VAR2"])
        self.assertEqual(post.call_args.kwargs["headers"]["authkey"], "key123")

    def test_provider_rejection(self):
        with mock.patch.dict(os.environ, {"MSG91_AUTH_KEY": "k", "MSG91_FLOW_ID": "f"}), \
             mock.patch("notifier.services.sms_service.requests.post") as post:
            post.return_value.json.return_value = {"type": "error"}
            result = sms_service.send_reminder_sms(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "send_rejected")


class CallServiceTests(TestCase):
    def setUp(self):
        self.person = make_person(phone="+919000000022", language="te-IN")
        self.event = make_event(self.person)
        self.package = {"text_local": "తెలుగు రిమైండర్", "text_english": "Telugu reminder"}

    def test_not_configured(self):
        with mock.patch.object(call_service, "_voice_configured", return_value=False):
            result = call_service.place_call(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_configured")

    def test_payload_shape(self):
        with mock.patch.dict(os.environ, VOICE_ENV), \
             mock.patch("notifier.services.call_service.requests.post") as post:
            post.return_value.json.return_value = {"attempt_id": "att-123"}
            result = call_service.place_call(self.event, self.package)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["attempt_id"], "att-123")
        self.assertEqual(result["language"], "Telugu")

        url = post.call_args.args[0]
        self.assertIn("/orgs/org/workspaces/ws/outbounds", url)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["user_config"]["user_phone_number"], "+919000000022")

        overrides = payload["app_config"]["app_overrides"]
        self.assertNotIn("initial_bot_message", overrides, "the flow carries the call; no opening override")
        self.assertEqual(overrides["initial_language_name"], "Telugu")

        # reminder_text (the translated reminder) is the default variable.
        variables = payload["app_config"]["agent_variables"]
        self.assertEqual(variables, {"reminder_text": "తెలుగు రిమైండర్"})

        self.assertEqual(payload["webhook_config"]["metadata"]["event_id"], self.event.id)
        self.assertIn("token=tok", payload["webhook_config"]["url"])
        self.assertEqual(post.call_args.kwargs["headers"]["X-API-Key"], "vk")

    def test_webhook_omitted_without_token(self):
        env = {**VOICE_ENV, "BOLO_WEBHOOK_TOKEN": ""}
        with mock.patch.dict(os.environ, env), \
             mock.patch("notifier.services.call_service.requests.post") as post:
            post.return_value.json.return_value = {"attempt_id": "a"}
            call_service.place_call(self.event, self.package)
        self.assertNotIn("webhook_config", post.call_args.kwargs["json"])

    def test_no_attempt_id_is_failure(self):
        with mock.patch.dict(os.environ, VOICE_ENV), \
             mock.patch("notifier.services.call_service.requests.post") as post:
            post.return_value.json.return_value = {}
            result = call_service.place_call(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_attempt_id")

    def test_api_error_body_captured_in_detail(self):
        error_response = mock.Mock()
        error_response.text = '{"error":{"data":{"details":"app_version: not found"}}}'
        with mock.patch.dict(os.environ, VOICE_ENV), \
             mock.patch("notifier.services.call_service.requests.post") as post:
            post.return_value.raise_for_status.side_effect = requests.HTTPError(
                "422 Client Error: Unprocessable Entity", response=error_response
            )
            result = call_service.place_call(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "call_failed")
        self.assertIn("app_version: not found", result["detail"])

    def test_agent_vars_payload(self):
        """Flows that declare more variables get their values per call."""
        env = {
            **VOICE_ENV,
            "SARVAM_VOICE_AGENT_VARS": "person_name,reminder_text,appointment_time",
        }
        with mock.patch.dict(os.environ, env), \
             mock.patch("notifier.services.call_service.requests.post") as post:
            post.return_value.json.return_value = {"attempt_id": "att-vars"}
            result = call_service.place_call(self.event, self.package)
        self.assertTrue(result["ok"], result)

        payload = post.call_args.kwargs["json"]
        overrides = payload["app_config"]["app_overrides"]
        self.assertNotIn("initial_bot_message", overrides, "the flow carries the call; no opening override")

        variables = payload["app_config"]["agent_variables"]
        self.assertEqual(variables["person_name"], self.person.name)
        self.assertEqual(variables["reminder_text"], "తెలుగు రిమైండర్")
        self.assertIn(":", variables["appointment_time"], "local datetime formatted for the flow")

    def test_unknown_variable_names_are_not_sent(self):
        env = {**VOICE_ENV, "SARVAM_VOICE_AGENT_VARS": "person_name,banana"}
        with mock.patch.dict(os.environ, env), \
             mock.patch("notifier.services.call_service.requests.post") as post:
            post.return_value.json.return_value = {"attempt_id": "a"}
            call_service.place_call(self.event, self.package)
        variables = post.call_args.kwargs["json"]["app_config"]["agent_variables"]
        self.assertEqual(list(variables), ["person_name"])


class WhatsappServiceTests(TestCase):
    def setUp(self):
        self.person = make_person(phone="+919000000041")
        self.event = make_event(self.person)
        self.package = {"text_local": "नमस्ते, यह अनुस्मारक है।", "text_english": "Hi, this is a reminder."}

    def test_not_configured(self):
        with mock.patch.object(whatsapp_service, "_whatsapp_configured", return_value=False):
            result = whatsapp_service.send_reminder_message(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_configured")

    def test_payload_shape(self):
        with mock.patch.dict(
            os.environ, {"WHATSAPP_PHONE_NUMBER_ID": "pnid", "WHATSAPP_ACCESS_TOKEN": "wtok"}
        ), mock.patch("notifier.services.whatsapp_service.requests.post") as post:
            post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
            result = whatsapp_service.send_reminder_message(self.event, self.package)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["message_id"], "wamid.1")
        self.assertIn("/notifier/respond/", result["link"])

        self.assertIn("/pnid/messages", post.call_args.args[0])
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["to"], "919000000041", "leading plus must be stripped")
        interactive = payload["interactive"]
        self.assertIn("नमस्ते", interactive["body"]["text"])
        self.assertIn("/notifier/respond/", interactive["body"]["text"])
        buttons = interactive["action"]["buttons"]
        self.assertEqual(buttons[0]["reply"]["id"], f"bolo:{self.event.id}:1")
        self.assertEqual(buttons[1]["reply"]["id"], f"bolo:{self.event.id}:2")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer wtok")

    def test_provider_rejection(self):
        with mock.patch.dict(
            os.environ, {"WHATSAPP_PHONE_NUMBER_ID": "p", "WHATSAPP_ACCESS_TOKEN": "t"}
        ), mock.patch("notifier.services.whatsapp_service.requests.post") as post:
            post.return_value.json.return_value = {"messages": []}
            result = whatsapp_service.send_reminder_message(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "send_rejected")

    def test_send_exception_is_failure_not_crash(self):
        with mock.patch.dict(
            os.environ, {"WHATSAPP_PHONE_NUMBER_ID": "p", "WHATSAPP_ACCESS_TOKEN": "t"}
        ), mock.patch("notifier.services.whatsapp_service.requests.post") as post:
            import requests as requests_lib

            post.side_effect = requests_lib.RequestException("boom")
            result = whatsapp_service.send_reminder_message(self.event, self.package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "send_failed")


class RealDeliveryRoutingTests(SarvamMockMixin, TestCase):
    """Delivery routes through the real SMS / call services when configured."""

    def _dispatch_one(self, person):
        make_event(person)
        return dispatch_due_reminders(confirm=True)["results"][0]

    def test_sms_configured_sends(self):
        person = make_person(
            phone="+919000000031", channel_preference=Person.ChannelPreference.SMS
        )
        with mock.patch.object(
            delivery.sms_service, "send_reminder_sms",
            return_value={"ok": True, "provider": "msg91",
                          "link": "http://192.168.1.15:8000/notifier/respond/1/"},
        ) as send:
            delivery_info = self._dispatch_one(person)["delivery"]
        self.assertEqual(delivery_info["mode"], "sent:msg91")
        self.assertIn("/notifier/respond/", delivery_info["link"])
        send.assert_called_once()

    def test_call_configured_places_call(self):
        person = make_person(
            phone="+919000000032", channel_preference=Person.ChannelPreference.VOICE_CALL
        )
        with mock.patch.object(
            delivery.call_service, "place_call",
            return_value={"ok": True, "attempt_id": "att-9", "language": "Hindi"},
        ) as place:
            delivery_info = self._dispatch_one(person)["delivery"]
        self.assertEqual(delivery_info["mode"], "call_placed")
        self.assertEqual(delivery_info["attempt_id"], "att-9")
        place.assert_called_once()

    def test_whatsapp_configured_sends(self):
        person = make_person(
            phone="+919000000033", channel_preference=Person.ChannelPreference.WHATSAPP
        )
        with mock.patch.object(
            delivery.whatsapp_service,
            "send_reminder_message",
            return_value={
                "ok": True,
                "provider": "whatsapp_cloud",
                "message_id": "wamid.9",
                "link": "http://192.168.1.15:8000/notifier/respond/1/",
            },
        ) as send:
            delivery_info = self._dispatch_one(person)["delivery"]
        self.assertEqual(delivery_info["mode"], "sent:whatsapp_cloud")
        self.assertEqual(delivery_info["message_id"], "wamid.9")
        self.assertIn("/notifier/respond/", delivery_info["link"])
        send.assert_called_once()


class CallOutcomeWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.person = make_person()
        self.event = make_event(self.person, voice_dispatched_at=timezone.now())
        self.env = mock.patch.dict(os.environ, {"BOLO_WEBHOOK_TOKEN": "tok"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def post_payload(self, payload, token="tok"):
        url = f"/notifier/webhook/call/?token={token}" if token else "/notifier/webhook/call/"
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def connected_payload(self, **overrides):
        payload = {
            "attempt_id": "att",
            "status": "connected",
            "webhook_config": {"url": "x", "metadata": {"event_id": self.event.id}},
            "final_agent_variables": {"client_reply": "Yes"},
            "interaction_transcript": [
                {"role": "agent", "en_text": "Hello, this is your reminder."},
                {"role": "user", "en_text": "Yes, I will be there."},
            ],
        }
        payload.update(overrides)
        return payload

    def test_connected_yes_records_confirmation(self):
        response = self.post_payload(self.connected_payload())
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)
        self.assertEqual(self.event.response_channel, "voice_call")

    def test_connected_no_records_decline(self):
        response = self.post_payload(self.connected_payload(final_agent_variables={"client_reply": "nahi"}))
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.DECLINED)

    def test_no_answer_leaves_pending_for_retry_ladder(self):
        response = self.post_payload({
            "attempt_id": "b",
            "status": "no_answer",
            "webhook_config": {"url": "x", "metadata": {"event_id": self.event.id}},
        })
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.PENDING)

    def test_connected_without_reply_variable_changes_nothing(self):
        response = self.post_payload(self.connected_payload(final_agent_variables={}))
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.PENDING)

    def test_missing_token_unauthorized(self):
        response = self.post_payload({"status": "no_answer"}, token=None)
        self.assertEqual(response.status_code, 401)


class WhatsappWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.person = make_person(phone="+919000000042")
        self.event = make_event(self.person, voice_dispatched_at=timezone.now())
        self.env = mock.patch.dict(os.environ, {"BOLO_WEBHOOK_TOKEN": "tok"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def post_payload(self, payload, **extra):
        return self.client.post(
            "/notifier/webhook/whatsapp/",
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    def button_payload(self, reply_id, title="Confirm"):
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "919000000042",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {"id": reply_id, "title": title},
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

    def text_payload(self, body, wa_id="919000000042"):
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {"from": wa_id, "type": "text", "text": {"body": body}}
                                ]
                            }
                        }
                    ]
                }
            ]
        }

    def test_hub_verification_echoes_challenge(self):
        response = self.client.get(
            "/notifier/webhook/whatsapp/",
            {"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "chal-42"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "chal-42")

    def test_hub_verification_rejects_wrong_token(self):
        response = self.client.get(
            "/notifier/webhook/whatsapp/", {"hub.verify_token": "nope", "hub.challenge": "x"}
        )
        self.assertEqual(response.status_code, 401)

    def test_disabled_without_env_token(self):
        os.environ.pop("BOLO_WEBHOOK_TOKEN", None)
        try:
            response = self.client.get("/notifier/webhook/whatsapp/", {"hub.verify_token": "tok"})
        finally:
            os.environ["BOLO_WEBHOOK_TOKEN"] = "tok"
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "webhook_disabled")

    def test_button_reply_records_confirmation(self):
        response = self.post_payload(self.button_payload(f"bolo:{self.event.id}:1"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recorded"], 1)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)
        self.assertEqual(self.event.response_channel, "whatsapp")

    def test_button_reply_records_decline(self):
        response = self.post_payload(
            self.button_payload(f"bolo:{self.event.id}:2", title="Decline")
        )
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.DECLINED)

    def test_text_reply_matches_by_phone(self):
        response = self.post_payload(self.text_payload("haan"))
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.CONFIRMED)
        self.assertEqual(self.event.response_channel, "whatsapp")

    def test_unrecognized_text_stays_pending(self):
        response = self.post_payload(self.text_payload("maybe later"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recorded"], 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.PENDING)

    def test_reply_from_unknown_number_is_ignored(self):
        response = self.post_payload(self.text_payload("yes", wa_id="919000000099"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recorded"], 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.response_value, Event.ResponseValue.PENDING)

    def test_signature_verified_when_app_secret_set(self):
        os.environ["WHATSAPP_APP_SECRET"] = "shh"
        try:
            body = json.dumps(self.button_payload(f"bolo:{self.event.id}:1"))
            digest = hmac.new(b"shh", body.encode(), hashlib.sha256).hexdigest()
            ok = self.client.post(
                "/notifier/webhook/whatsapp/",
                data=body,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256=f"sha256={digest}",
            )
            self.assertEqual(ok.status_code, 200)
            bad = self.client.post(
                "/notifier/webhook/whatsapp/",
                data=body,
                content_type="application/json",
                HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef",
            )
            self.assertEqual(bad.status_code, 401)
        finally:
            os.environ.pop("WHATSAPP_APP_SECRET", None)
