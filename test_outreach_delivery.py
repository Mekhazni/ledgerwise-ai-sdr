import asyncio
import base64
import json
import os
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import main
import outreach_delivery
from test_demo_fit import first_email


class DeliveryTests(unittest.TestCase):
    actor = {"user_id": "reviewer-id", "role": "reviewer"}
    env = {"APP_BASE_URL": "http://testserver", "TEST_EMAIL": "demo@example.com",
           "GMAIL_SEND_ENABLED": "true", "GMAIL_SENDER_EMAIL": "sender@gmail.com",
           "GMAIL_CLIENT_ID": "mock", "GMAIL_CLIENT_SECRET": "mock", "GMAIL_REFRESH_TOKEN": "mock"}
    origin = {"origin": "http://testserver"}

    def test_version_claim_is_required_even_on_double_click(self):
        message = {"id": 5, "thread_id": 2, "direction": "out", "approved_version": 5,
                   "version": 5, "status": "approved", "actual_recipient": "demo@example.com",
                   "subject": "Bookkeeping for IQBAR", "body": first_email()}
        result = {"status": "claimed", "message": message, "thread": {"id": 2, "demo_only": False}}
        claimed = False

        async def db(path, key, *, json, **kwargs):
            nonlocal claimed
            if path.endswith("review_initial_message"):
                self.assertEqual(json["p_recipient"], "demo@example.com")
                if claimed or json["p_version"] != 5:
                    return httpx.Response(200, json={"status": "stale"})
                claimed = True
                return httpx.Response(200, json=result)
            self.assertEqual(json["p_gmail_thread"], "thread-id")
            return httpx.Response(200, json=True)

        with patch.dict(os.environ, self.env), patch.object(
            main, "require_account", new=AsyncMock(return_value=self.actor)
        ), patch.object(main, "supabase", side_effect=db), patch.object(
            main, "deliver_test_email", new=AsyncMock(return_value=("sent", None, {"id": "message-id", "threadId": "thread-id"}))
        ) as sender:
            async def run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
                    route = "/drafts/messages/5/approve"
                    stale = await client.post(route, headers=self.origin, data={"version": 4})
                    self.assertEqual(stale.status_code, 409)
                    return await asyncio.gather(*[
                        client.post(route, headers=self.origin, data={"version": 5, "recipient": "real@example.com",
                                    "cc": "other@example.com", "subject": "tampered"}) for _ in range(2)
                    ])
            outcomes = asyncio.run(run())
            self.assertEqual(sorted(r.status_code for r in outcomes), [200, 409])
            sender.assert_awaited_once()
            self.assertEqual(sender.await_args.args[0]["subject"], message["subject"])
            self.assertEqual(sender.await_args.args[1], "demo@example.com")

    def test_missing_test_email_and_unauthorized_action_do_not_claim(self):
        client = TestClient(main.app)
        with patch.dict(os.environ, {**self.env, "TEST_EMAIL": ""}), patch.object(
            main, "require_account", new=AsyncMock(side_effect=[self.actor, main.HTTPException(403)])
        ), patch.object(main, "supabase", new=AsyncMock()) as db:
            route = "/drafts/messages/5/approve"
            self.assertEqual(client.post(route, headers=self.origin, data={"version": 5}).status_code, 503)
            self.assertEqual(client.post(route, headers=self.origin, data={"version": 5}).status_code, 403)
            db.assert_not_awaited()

    def test_edit_then_reject_use_exact_versions_without_sending(self):
        reads = [httpx.Response(200, json=[{"facts_used": [], "initial_outreach": {"contact_id": 10},
            "threads": {"companies": {"name": "IQBAR"}}}]),
            httpx.Response(200, json={"status": "edited"}),
            httpx.Response(200, json={"status": "rejected"})]
        with patch.dict(os.environ, self.env), patch.object(
            main, "require_account", new=AsyncMock(return_value=self.actor)
        ), patch.object(main, "supabase", new=AsyncMock(side_effect=reads)) as db, patch.object(
            main, "verify_edited_copy", return_value=("Edited", first_email())
        ) as check, patch.object(main, "deliver_test_email", new=AsyncMock()) as sender:
            client = TestClient(main.app)
            response = client.post("/drafts/messages/5/edit", headers=self.origin,
                                   data={"version": 5, "subject": "Edited", "body": first_email()})
            self.assertEqual(response.json(), {"status": "edited"})
            check.assert_called_once()
            self.assertEqual(db.await_args.kwargs["json"]["p_version"], 5)
            response = client.post("/drafts/messages/6/reject", headers=self.origin, data={"version": 6})
            self.assertEqual(response.json(), {"status": "rejected"})
            self.assertEqual(db.await_args.kwargs["json"]["p_version"], 6)
            sender.assert_not_awaited()

    def test_gmail_mime_has_only_test_recipient_and_timeout_is_unknown(self):
        captured = []
        async def mail(request):
            captured.append(request)
            if request.url.host == "oauth2.googleapis.com":
                return httpx.Response(200, json={"access_token": "mock"})
            body = json.loads(request.content)
            email = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(body["raw"]))
            self.assertEqual(email["To"], "demo@example.com")
            self.assertIsNone(email["Cc"])
            self.assertIsNone(email["Bcc"])
            self.assertIsNone(email["In-Reply-To"])
            self.assertNotIn("threadId", body)
            raise httpx.ReadTimeout("mock timeout", request=request)

        client_factory = httpx.AsyncClient
        with patch.dict(os.environ, self.env), patch.object(
            outreach_delivery.httpx, "AsyncClient",
            side_effect=lambda **kw: client_factory(transport=httpx.MockTransport(mail), **kw)
        ):
            result, detail, _ = asyncio.run(outreach_delivery.deliver_test_email(
                {"id": 5, "version": 5, "subject": "IQBAR", "body": first_email()},
                "demo@example.com", outreach_delivery.gmail_settings()))
        self.assertEqual(result, "unknown")
        self.assertIn("Do not resend", detail)
        self.assertEqual(len(captured), 2)

    def test_sent_conversation_shows_approved_version_and_history(self):
        threads = [{"id": 2, "gmail_thread_id": "mock", "companies": {"name": "IQBAR"}}]
        older = {"id": 3, "subject": "Older subject", "body": "Old text", "version": 4,
                 "status": "rejected", "review_events": []}
        sent = {"id": 5, "subject": "Approved subject", "body": "Approved text", "version": 5,
                "status": "sent", "actual_recipient": "demo@example.com", "sent_at": "2026-11-20T00:00:00Z",
                "initial_outreach": {"name": "Will", "business_email": "contact@example.com"},
                "review_events": [{"action": "approve", "name": "Reviewer", "at": "2026-11-20"}]}
        with patch.object(main, "signed_in_account", new=AsyncMock(return_value=self.actor)), patch.object(
            main, "supabase", new=AsyncMock(side_effect=[httpx.Response(200, json=threads),
                                                         httpx.Response(200, json=[older, sent])])
        ):
            response = TestClient(main.app).get("/dashboard/conversations?thread_id=2")
        self.assertEqual(response.status_code, 200)
        for value in ("OUT", "Approved text", "Old text", "Reviewer", "Version 5"):
            self.assertIn(value, response.text)
