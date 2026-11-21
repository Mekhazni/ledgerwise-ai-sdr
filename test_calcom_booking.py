import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import main
from calcom_booking import Calcom, acceptance, instant, proposed_time, scheduling_request, stamp


SETTINGS = {"CALCOM_API_KEY": "mock", "CALCOM_EVENT_TYPE_ID": "42", "CALCOM_TIME_ZONE": "America/New_York",
            "CALCOM_BOOKING_ENABLED": "true"}
START = stamp((datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0))
SLOT = {"start": START, "zone": "America/New_York", "option": 1}


class BookingTests(unittest.IsolatedAsyncioTestCase):
    def test_acceptance_requires_one_option_and_phone_is_optional(self):
        self.assertEqual(acceptance("I confirm option 1. Phone: +1 202-555-0123", [SLOT]),
                         (SLOT, "+12025550123"))
        self.assertEqual(acceptance("Option 1", [SLOT]), (SLOT, None))
        exact = instant(START).strftime("%Y-%m-%d at %H:%M UTC")
        # Cal.com slots start on the minute.
        offered = {**SLOT, "start": stamp(instant(START).replace(second=0))}
        self.assertEqual(acceptance(f"I confirm {exact}", [offered]), (offered, None))
        self.assertEqual(acceptance(f"How about {exact}?", [offered]), (None, None))
        self.assertEqual(acceptance("Option 1 +10000000000", [SLOT]), (SLOT, None))
        for message in ("Yes", "Option 1 or option 2", "Don't book option 1",
                        "Option 1. Please unsubscribe.", "+12025550123"):
            self.assertEqual(acceptance(message, [SLOT]), (None, None))
        followup = {**SLOT, "phone_requested": True}
        self.assertEqual(acceptance("Phone: +12025550123", [followup]), (None, None))

    def test_explicit_booking_confirmation_variations(self):
        for text in ("Option 1 works for me. Please book it.",
                     "Option 1 works. Please book that slot!",
                     "Option 1 works for me.\nGo ahead and book it.",
                     "Hi,\n\nOption 1 works for me. Please book it.\n\nBest,\nWill\n\n> Option 2"):
            with self.subTest(text=text):
                self.assertEqual(acceptance(text, [SLOT]), (SLOT, None))
        for text in ("Please book it.", "Option 1 or option 2. Please book it.",
                     "Option 1 may work. Please book it.", "Option 1. Please book it. Unsubscribe."):
            self.assertEqual(acceptance(text, [SLOT]), (None, None))

    async def test_calcom_payload_uses_configured_event_and_mock_attendee(self):
        def respond(request):
            if request.method == "GET":
                self.assertEqual(request.url.params["eventTypeId"], "42")
                return httpx.Response(200, json={"status": "success", "data": {"day": [{"start": START}]}})
            import json
            payload = json.loads(request.content)
            self.assertEqual(payload["attendee"]["email"], "controlled-test@example.com")
            self.assertEqual(payload["start"], START)
            self.assertNotIn("phoneNumber", payload["attendee"])
            self.assertEqual(payload["metadata"]["ledgerwiseAttempt"], "8")
            return httpx.Response(201, json={"status": "success", "data": {
                "uid": "mock-uid", "start": START, "status": "accepted",
            }})
        with patch.dict(os.environ, SETTINGS):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                cal = Calcom(client)
                draft = await cal.offer()
                self.assertEqual(draft["offered_slots"], [SLOT])
                self.assertIn("Option 1:", draft["body"])
                self.assertNotIn("phone", draft["body"].lower())
                result = await cal.book(SLOT, "controlled-test@example.com", "Test attendee", 8)
                self.assertEqual(result["uid"], "mock-uid")

    async def test_unavailable_accepted_slot_drafts_alternatives_without_booking(self):
        incoming = {"id": 8, "body": "Option 1", "received_at": "2026-02-01T16:00:00Z"}
        offer = {"id": 7, "sent_at": "2026-02-01T15:00:00Z", "offered_slots": [SLOT]}
        with patch.dict(os.environ, SETTINGS), patch.object(main, "Calcom") as factory:
            factory.return_value.slots = AsyncMock(return_value=[])
            factory.return_value.offer = AsyncMock(return_value={"body": "New options"})
            result = await main.scheduling_draft(incoming, [offer], {"contact_name": "Test"}, {}, "auto")
            self.assertEqual(result, {"body": "New options"})
            factory.return_value.book.assert_not_called()

    async def test_next_week_request_regenerates_as_offer_in_local_calendar_week(self):
        from zoneinfo import ZoneInfo
        text = "I'm interested in learning more about Ledgerwise. Could we schedule a call next week?"
        incoming = {"id": 8, "body": text, "received_at": "2026-02-01T16:00:00Z"}
        company = {"name": "Company"}
        async def available(start, end):
            zone = ZoneInfo("America/New_York")
            local_start, local_end = start.astimezone(zone), end.astimezone(zone)
            self.assertEqual((local_start.weekday(), local_start.hour), (0, 0))
            self.assertEqual((local_end - local_start).days, 7)
            return [{**SLOT, "start": stamp(start + timedelta(hours=10))}, {**SLOT, "start": stamp(end)}]
        with patch.dict(os.environ, SETTINGS), patch.object(Calcom, "slots", new=AsyncMock(side_effect=available)), patch.object(
            Calcom, "book", new=AsyncMock()
        ) as book:
            draft = await main.scheduling_draft(incoming, [], company, {}, "regenerate")
            self.assertEqual(len(draft["offered_slots"]), 1)
            self.assertIn("Option 1", draft["body"])
            self.assertNotIn("from our offer", draft["body"])
            book.assert_not_awaited()

    def test_saved_wrapped_next_week_messages_regenerate_through_endpoint(self):
        incoming = {
            "id": 68, "thread_id": 1, "subject": "Re: IQBAR", "direction": "in",
            "received_at": "2026-11-21T10:00:00Z",
            "body": "Hi,\n\nI'm interested in learning more about Ledgerwise. Could we schedule a call\n"
                    "next week?\n\nBest,\nWill",
        }
        gina = {
            **incoming, "id": 891, "thread_id": 3, "subject": "Re: Simple Sugars",
            "body": "Hi,\n\nThanks for the information!\n\nI'd like to schedule a call with Ledgerwise next week. "
                    "Could you share a\nfew available time slots?\n\nBest,\nGina",
        }
        for message, company, version_id in ((incoming, "IQBAR", 75), (gina, "Simple Sugars", 918)):
            with self.subTest(message_id=message["id"]):
                responses = [httpx.Response(200, json=body) for body in (
                    {"status": "generate", "company_id": 25, "token": "mock"},
                    [message], [message], True,
                )]
                async def available(start, end):
                    return [{**SLOT, "start": stamp(start + timedelta(hours=10))}]
                with patch.dict(os.environ, {**SETTINGS, "APP_BASE_URL": "http://testserver"}), patch.object(
                    main, "require_account", new=AsyncMock(return_value={"user_id": "reviewer"})
                ), patch.object(main, "supabase", new=AsyncMock(side_effect=responses)) as db, patch.object(
                    main, "read_companies", new=AsyncMock(return_value=[{"name": company, "company_contacts": []}])
                ), patch.object(Calcom, "slots", new=AsyncMock(side_effect=available)) as slots, patch.object(
                    Calcom, "book", new=AsyncMock()
                ) as book, patch.object(main, "generate_reply") as gemini:
                    response = TestClient(main.app).post(f"/conversations/incoming/{message['id']}/generate",
                        headers={"origin": "http://testserver"}, data={"version_id": version_id})
                    self.assertEqual(response.json(), {"status": "ready"})
                    self.assertEqual(db.await_args_list[0].kwargs["json"]["p_version_id"], version_id)
                    self.assertEqual(db.await_args.args[0], "rest/v1/rpc/finish_scheduling_reply")
                    draft = db.await_args.kwargs["json"]["p_result"]
                    self.assertEqual(len(draft["offered_slots"]), 1)
                    self.assertIn("Option 1:", draft["body"])
                    slots.assert_awaited_once()
                    book.assert_not_awaited()
                    gemini.assert_not_called()

    def test_scheduling_classification_is_independent_of_gmail_wrapping(self):
        for phrase, expected in (("next week", "next_week"), ("tomorrow", "clarify"),
                                 ("this Friday", "clarify"), ("next Monday", "clarify")):
            for space in (" ", "  ", "\n", "\r\n ", "\t"):
                with self.subTest(phrase=phrase, space=space):
                    text = "Could we schedule a call" + space + phrase.replace(" ", space) + "?"
                    self.assertEqual(scheduling_request(text, False), expected)
        self.assertEqual(scheduling_request("Can we meet on November 25,\n2034 at 2 PM\r\nET?", False),
                         proposed_time("Can we meet on November 25, 2034 at 2 PM ET?"))

    def test_unrelated_replies_do_not_enter_scheduler(self):
        for text in ("What's your pricing?", "Are you an AI assistant?", "What services do you offer?",
                     "Unsubscribe. Could we schedule a call next week?",
                     "Could I speak with a human? Can we meet next week?"):
            with self.subTest(text=text):
                self.assertIsNone(scheduling_request(text, False))
        self.assertEqual(scheduling_request("Could we meet next week at 2 PM?", False), "clarify")
        self.assertEqual(scheduling_request(
            "Could we schedule a call with Ledgerwise next week at 2 PM? Could you share available time slots?",
            False), "clarify")
        self.assertEqual(scheduling_request(
            "Could we schedule a call with Ledgerwise next week? What services do you provide?", False), "clarify")
        self.assertEqual(scheduling_request("Yes.", True), "clarify")

    async def test_proposal_checks_availability_and_never_books(self):
        text = "Can we meet on November 25, 2034 at 2 PM ET?"
        self.assertEqual(stamp(proposed_time(text)), "2034-11-25T19:00:00Z")
        self.assertIsNone(proposed_time("Can we meet on November 25, 2034 at 2 PM?"))
        self.assertIsNone(proposed_time("2034-11-05 at 1:30 AM ET"))
        proposed = instant(START)
        with patch.dict(os.environ, SETTINGS):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
                cal = Calcom(client)
                with patch.object(cal, "slots", new=AsyncMock(side_effect=[[SLOT], [
                    {**SLOT, "start": stamp(proposed + timedelta(hours=1))}
                ]])):
                    exact = await cal.propose(proposed)
                    alternatives = await cal.propose(proposed)
        self.assertEqual(exact["offered_slots"], [SLOT])
        self.assertIn("to confirm", exact["body"])
        self.assertEqual(alternatives["offered_slots"][0]["start"], stamp(proposed + timedelta(hours=1)))
        self.assertIn("unavailable", alternatives["body"])

    async def test_proposals_and_ambiguity_route_separately(self):
        incoming = {"id": 8, "body": "Can we meet Tuesday at 2 PM?", "received_at": "2026-02-01T16:00:00Z"}
        with patch.dict(os.environ, SETTINGS), patch.object(main, "Calcom") as factory:
            draft = await main.scheduling_draft(incoming, [], {}, {}, "auto")
            self.assertIn("time zone", draft["body"])
            self.assertNotIn("from our offer", draft["body"])
            factory.assert_not_called()
            incoming["body"] = "How about 2034-11-25 at 2 PM ET?"
            factory.return_value.propose = AsyncMock(return_value={"body": "Please confirm Option 1"})
            draft = await main.scheduling_draft(incoming, [], {}, {}, "auto")
            self.assertIn("Please confirm", draft["body"])
            factory.return_value.book.assert_not_called()

    async def test_booking_without_phone_is_recorded_before_confirmation_draft(self):
        incoming = {"id": 8, "body": "Option 1", "received_at": "2026-02-01T16:00:00Z"}
        offer = {"id": 7, "sent_at": "2026-02-01T15:00:00Z", "offered_slots": [SLOT]}
        with patch.dict(os.environ, SETTINGS), patch.object(
            main, "configured_test_email", return_value="controlled-test@example.com"
        ), patch.object(main, "Calcom") as factory, patch.object(
            main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=True))
        ) as db, patch.object(main, "deliver_test_email", new=AsyncMock()) as sender:
            cal = factory.return_value
            cal.zone = SLOT["zone"]
            cal.slots = AsyncMock(return_value=[SLOT])
            cal.book = AsyncMock(return_value={"uid": "confirmed", "meetingUrl": "https://example.com/meeting"})
            draft = await main.scheduling_draft(incoming, [offer], {"name": "Company", "contact_name": "Test Contact"}, {"token": "t"}, "auto")
            self.assertIn("Your call is confirmed", draft["body"])
            self.assertEqual([call.args[0] for call in db.await_args_list], [
                "rest/v1/rpc/reserve_calcom_booking", "rest/v1/rpc/record_calcom_booking"])
            self.assertEqual(db.await_args_list[0].kwargs["json"]["p_email"], "controlled-test@example.com")
            self.assertIsNone(db.await_args_list[0].kwargs["json"]["p_phone"])
            sender.assert_not_awaited()

    async def test_reservation_blocks_duplicate_and_timeout_does_not_retry(self):
        incoming = {"id": 8, "body": "Option 1 +12025550123", "received_at": "2026-02-01T16:00:00Z"}
        offer = {"id": 7, "sent_at": "2026-02-01T15:00:00Z", "offered_slots": [SLOT]}
        for claimed in (False, True):
            with self.subTest(claimed=claimed), patch.dict(os.environ, SETTINGS), patch.object(
                main, "configured_test_email", return_value="controlled-test@example.com"
            ), patch.object(main, "Calcom") as factory, patch.object(
                main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=claimed))
            ) as db:
                cal = factory.return_value
                cal.slots = AsyncMock(return_value=[SLOT])
                cal.book = AsyncMock(side_effect=httpx.ReadTimeout("mock timeout"))
                result = await main.scheduling_draft(incoming, [offer], {"name": "Company", "contact_name": "Test Contact"}, {"token": "t"}, "auto")
                self.assertTrue(result["needs_human"])
                self.assertEqual(cal.book.await_count, int(claimed))
                self.assertEqual(db.await_count, 1)


class SavedConfirmationTests(unittest.TestCase):
    def test_saved_confirmation_is_retryable_without_booking_until_enabled(self):
        incoming = {"id":87,"thread_id":1,"direction":"in","subject":"Re: IQBAR",
                    "body":"Hi,\n\nOption 1 works for me. Please book it.\n\nBest,\nWill",
                    "received_at":"2026-11-21T10:00:00Z"}
        offer = {"id":76,"direction":"out","body":"Option 1", "sent_at":"2026-11-21T09:00:00Z","offered_slots":[SLOT]}
        for enabled in (False, True):
            replies = [{"status":"generate","company_id":1,"token":"t"}, [incoming], [incoming,offer]]
            replies += [True,True,True] if enabled else [False]
            with self.subTest(enabled=enabled), patch.dict(os.environ,{
                **SETTINGS,"APP_BASE_URL":"http://testserver","CALCOM_BOOKING_ENABLED":str(enabled).lower()
            }), patch.object(main,"supabase",new=AsyncMock(side_effect=[
                httpx.Response(200,json=item) for item in replies
            ])) as db, patch.object(main,"read_companies",new=AsyncMock(return_value=[
                {"name":"IQBAR","contact_name":"Will","company_contacts":[]}
            ])), patch.object(main,"configured_test_email",return_value="test@example.com"), patch.object(
                main,"require_account",new=AsyncMock(return_value={"user_id":"reviewer"})
            ), patch.object(main,"Calcom") as cal, patch.object(main,"generate_reply") as gemini:
                cal.return_value.slots=AsyncMock(return_value=[SLOT])
                cal.return_value.book=AsyncMock(return_value={"uid":"mock-booking"})
                if enabled:
                    response=TestClient(main.app).post("/conversations/incoming/87/generate",
                        headers={"origin":"http://testserver"},data={"process_booking":"true","version_id":88})
                    self.assertEqual(response.json(),{"status":"ready"})
                    self.assertEqual(db.await_args_list[0].kwargs["json"]["p_version_id"],88)
                    self.assertEqual(db.await_args_list[3].kwargs["json"]["p_offer"],76)
                    cal.return_value.book.assert_awaited_once()
                    self.assertIn("Your call is confirmed",db.await_args.kwargs["json"]["p_result"]["body"])
                else:
                    import asyncio
                    self.assertEqual(asyncio.run(main.run_ai_reply(87)),"failed")
                    self.assertIsNone(db.await_args.kwargs["json"]["p_result"])
                    self.assertIn("Booking disabled",db.await_args.kwargs["json"]["p_error"])
                    cal.assert_not_called()
                gemini.assert_not_called()


class ReviewerSchedulingTests(unittest.TestCase):
    def test_reviewer_scheduling_denies_non_owner_and_opted_out_company(self):
        for rows in ([], [{"companies":{"status":"opted_out"}}]):
            with self.subTest(rows=rows), patch.object(
                main,"require_account",new=AsyncMock(return_value={"user_id":"reviewer"})
            ), patch.object(main,"supabase",new=AsyncMock(return_value=httpx.Response(200,json=rows))) as db, patch.object(
                main,"Calcom"
            ) as cal:
                response=TestClient(main.app).get("/conversations/threads/2/schedule")
                self.assertEqual(response.status_code,409)
                self.assertEqual(db.await_args.kwargs["params"]["owner"],"eq.reviewer")
                cal.assert_not_called()

    def test_month_slots_are_grouped_in_event_timezone_and_rechecked_across_dates(self):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(SLOT["zone"])
        first = instant(START)
        first_day = first.astimezone(zone)
        second = first + timedelta(days=35)
        other = {**SLOT,"start":stamp(second)}
        context = ({"user_id":"reviewer"}, {"name":"Company","contact_name":"Contact"},
                   {"id":8,"body":"Let's meet","received_at":"2026-02-01T16:00:00Z"}, [])
        with patch.dict(os.environ,{**SETTINGS,"APP_BASE_URL":"http://testserver"}), patch.object(
            main,"reviewer_scheduling_context",new=AsyncMock(return_value=context)
        ), patch.object(main,"Calcom") as factory, patch.object(
            main,"supabase",new=AsyncMock(return_value=httpx.Response(200,json=True))
        ) as db:
            cal=factory.return_value
            cal.zone=SLOT["zone"]
            cal.slots=AsyncMock(side_effect=[[SLOT], [SLOT], [other], [SLOT], []])
            client=TestClient(main.app)
            root="/conversations/threads/2/schedule"
            data=client.get(root,params={"month":first_day.strftime("%Y-%m")}).json()
            self.assertEqual(data["timezone"],SLOT["zone"])
            self.assertEqual(data["slots"][0]["day"],first_day.date().isoformat())
            fields={"incoming_id":8,"starts":[START,other["start"]]}
            headers={"origin":"http://testserver"}
            self.assertEqual(client.post(root+"/offer",headers=headers,data=fields).status_code,200)
            self.assertEqual(len(db.await_args.kwargs["json"]["p_result"]["offered_slots"]),2)
            self.assertEqual(client.post(root+"/offer",headers=headers,data=fields).status_code,409)
            db.assert_awaited_once()

    def test_reviewer_sees_confirmation_and_can_save_phone_optional_booking_draft(self):
        incoming = {"id":8,"body":"Option 1","received_at":"2026-02-01T16:00:00Z"}
        offer = {"id":7,"sent_at":"2026-02-01T15:00:00Z","offered_slots":[SLOT]}
        context = ({"user_id":"reviewer"}, {"name":"Company","contact_name":"Test Contact"}, incoming, [offer])
        settings = {**SETTINGS,"APP_BASE_URL":"http://testserver","CALCOM_BOOKING_ENABLED":"false"}
        with patch.dict(os.environ, settings), patch.object(
            main, "reviewer_scheduling_context", new=AsyncMock(return_value=context)
        ), patch.object(main,"Calcom") as factory, patch.object(
            main,"configured_test_email",return_value="controlled-test@example.com"
        ), patch.object(main,"supabase",new=AsyncMock(return_value=httpx.Response(200,json=True))) as db:
            cal = factory.return_value
            cal.zone = SLOT["zone"]
            cal.slots = AsyncMock(return_value=[SLOT])
            cal.book = AsyncMock(return_value={"uid":"mock"})
            client = TestClient(main.app)
            root = "/conversations/threads/2/schedule"
            view = client.get(root).json()
            self.assertFalse(view["booking_enabled"])
            self.assertEqual(view["confirmation"]["message"], "Option 1")
            headers = {"origin":"http://testserver"}
            fields = {"incoming_id":8,"offer_id":7,"confirmed_start":START,"reviewed":"on"}
            self.assertEqual(client.post(root+"/book", headers=headers, data=fields).status_code,409)
            cal.book.assert_not_awaited()
            self.assertEqual(client.post(root+"/offer",headers=headers,
                                        data={"incoming_id":8,"starts":START}).status_code,200)
            self.assertIn("Option 1",db.await_args.kwargs["json"]["p_result"]["body"])
            with patch.dict(os.environ,{"CALCOM_BOOKING_ENABLED":"true"}):
                incoming["body"]="How about Option 1?"
                self.assertEqual(client.post(root+"/book",headers=headers,data=fields).status_code,409)
                cal.book.assert_not_awaited()
                incoming["body"]="Option 1"
                self.assertEqual(client.post(root+"/book",headers=headers,
                                            data={**fields,"reviewed":"false"}).status_code,409)
                cal.book.assert_not_awaited()
                self.assertEqual(client.post(root+"/book",headers=headers,data=fields).status_code,200)
            calls = {call.args[0]:call.kwargs["json"] for call in db.await_args_list}
            self.assertEqual(calls["rest/v1/rpc/reserve_calcom_booking"]["p_actor"],"reviewer")
            self.assertIsNone(calls["rest/v1/rpc/reserve_calcom_booking"]["p_phone"])
            self.assertIn("confirmed",calls["rest/v1/rpc/compose_reviewer_scheduling"]["p_result"]["body"])

class MeetingsPageTests(unittest.TestCase):
    def test_meetings_distinguishes_confirmed_booking_from_sent_offer(self):
        company = {"name": "Booked company", "contact_name": "Test Person"}
        booking = {"id": 4, "thread_id": 1, "start_time": START, "status": "booked",
                   "threads": {"companies": company},
                   "acceptance": {"review_events": [{"action": "booking_requested", "slot": SLOT,
                                                     "phone": "+12025550123", "email": "test@example.com"}]}}
        offer = {"id": 9, "thread_id": 2, "actual_recipient": "test@example.com", "offered_slots": [SLOT],
                 "threads": {"companies": {"name": "Pending company"}}}
        with patch.object(main, "signed_in_account", new=AsyncMock(return_value={"role": "reviewer"})), patch.object(
            main, "supabase", new=AsyncMock(side_effect=[
                httpx.Response(200, json=[booking]), httpx.Response(200, json=[offer])
            ])
        ):
            page = TestClient(main.app).get("/dashboard/meetings")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-meeting-status="Upcoming"', page.text)
        self.assertIn('data-meeting-status="Pending"', page.text)
        self.assertIn("+12025550123", page.text)
        self.assertIn('id="meetings-calendar"', page.text)
        self.assertIn('/dashboard/conversations?thread_id=1', page.text)
