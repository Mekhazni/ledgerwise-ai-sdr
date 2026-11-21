import asyncio
import base64
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import gmail_inbox
import main


def gmail_message(thread, key, *, ref=None, text="Thanks, tell me more.", sent=False, sender=None):
    headers = {"From": sender or ("sender@gmail.com" if sent else "demo@example.com"),
               "To": "demo@example.com" if sent else "sender@gmail.com",
               "Subject": "Bookkeeping", "Message-ID": f"<{key}@example.com>"}
    if ref:
        headers["In-Reply-To"] = ref
    text_part = {"mimeType": "text/plain", "body": {
        "data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    }}
    return {"threadId": thread, "id": key, "internalDate": "1795220000000",
            "labelIds": ["SENT" if sent else "INBOX"],
            "payload": {"mimeType": "multipart/alternative", "parts": [text_part],
                        "headers": [{"name": k, "value": v} for k, v in headers.items()]}}


class GmailInboxTests(unittest.TestCase):
    def test_opt_out_uses_current_reply_not_quoted_history(self):
        for body in ("Unsubscribe.", "Please stop emailing me.", "Not interested, thanks.",
                     "Thanks for reaching out.\nI'm not interested."):
            self.assertTrue(gmail_inbox.clear_opt_out(body), body)
        for body in ("Interested, thanks.\nOn Friday someone wrote:\nUnsubscribe.",
                     "Could you stop by next week?", "What happens if I unsubscribe?",
                     "Thanks!\n> Unsubscribe."):
            self.assertFalse(gmail_inbox.clear_opt_out(body), body)

    def test_sync_only_known_threads_and_keeps_shared_test_inbox_separate(self):
        data = {
            "a": [gmail_message("a", "sa", sent=True),
                  gmail_message("a", "follow-up", sent=True),
                  gmail_message("a", "ra", ref="<follow-up@example.com>", text="Not interested."),
                  gmail_message("a", "wrong-ref", ref="<sb@example.com>"),
                  gmail_message("a", "unsolicited", ref="<sa@example.com>", sender="stranger@example.com")],
            "b": [gmail_message("b", "sb", sent=True),
                  gmail_message("b", "rb", ref="<sb@example.com>"),
                  gmail_message("a", "wrong-thread", ref="<sb@example.com>")],
        }
        paths = []
        async def api(request):
            paths.append((request.method, request.url.host, request.url.path))
            if request.url.host == "oauth2.googleapis.com":
                return httpx.Response(200, json={"access_token": "mock"})
            thread = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": thread, "messages": data[thread]})

        known = [{"id": i, "gmail_thread_id": t, "messages": [{"actual_recipient": "demo@example.com",
                  "gmail_message_id": "s" + t}]} for i, t in enumerate(("a", "b"), 1)]
        known[0]["messages"].append({"actual_recipient": "demo@example.com", "gmail_message_id": "follow-up"})
        seen = set()
        async def save(thread_id, gmail_id, reply):
            key = reply["gmail_message_id"]
            self.assertEqual((thread_id, gmail_id), (1, "a") if key == "ra" else (2, "b"))
            if key in seen:
                return "duplicate"
            seen.add(key)
            return "suppressed" if reply["opt_out"] else "imported"

        env = {"GMAIL_SEND_ENABLED": "false", "GMAIL_SENDER_EMAIL": "sender@gmail.com",
               "GMAIL_CLIENT_ID": "mock", "GMAIL_CLIENT_SECRET": "mock", "GMAIL_REFRESH_TOKEN": "mock"}
        async_client = httpx.AsyncClient
        with patch.dict(os.environ, env), patch.object(
            gmail_inbox.httpx, "AsyncClient",
            side_effect=lambda **kw: async_client(transport=httpx.MockTransport(api), **kw),
        ):
            first = asyncio.run(gmail_inbox.sync_known_threads(known, save))
            second = asyncio.run(gmail_inbox.sync_known_threads(known, save))
        self.assertEqual(first, {"suppressed": 1, "imported": 1, "duplicate": 0, "skipped": 3, "failed": 0})
        self.assertEqual(second["duplicate"], 2)
        self.assertEqual(seen, {"ra", "rb"})
        self.assertEqual([method for method, host, _ in paths if host == "gmail.googleapis.com"], ["GET"] * 4)

    def test_import_fix_qualifies_joined_audit_column_and_preserves_deduplication(self):
        sql = Path(__file__).with_name("fix_gmail_reply_import.sql").read_text(encoding="utf-8")
        self.assertIn("review_events=d.review_events ||", sql)
        self.assertIn("on conflict (gmail_message_id) do nothing", sql)
        self.assertIn("latest.id=new_reply_id", sql)
        self.assertIn("select company_id into target_company from public.threads", sql)
        self.assertNotIn("review_events=review_events ||", sql)

    def test_sync_post_requires_account_and_records_opt_out(self):
        actor = {"role": "reviewer", "user_id": "mock"}
        thread = {"id": 2, "gmail_thread_id": "mock"}
        async def sync(threads, save):
            self.assertEqual(threads, [thread])
            self.assertEqual(await save(2, "mock", {"opt_out": True}), "suppressed")
            return {"suppressed": 1}
        with patch.dict(os.environ, {"APP_BASE_URL": "http://testserver"}), patch.object(
            main, "require_account", new=AsyncMock(side_effect=[main.HTTPException(403), actor])
        ), patch.object(main, "supabase", new=AsyncMock(side_effect=[
            httpx.Response(200, json=[thread]), httpx.Response(200, json="suppressed"),
        ])) as db, patch.object(main, "sync_known_threads", side_effect=sync) as inbox, patch.object(
            main, "conversations_page", new=AsyncMock(return_value=main.JSONResponse({}))
        ):
            client = TestClient(main.app)
            headers = {"origin": "http://testserver"}
            self.assertEqual(client.post("/conversations/sync", headers=headers).status_code, 403)
            response = client.post("/conversations/sync", headers=headers, data={"thread_id": "2"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertTrue(response.headers["location"].startswith("/dashboard/conversations?thread_id=2&"))
            inbox.assert_awaited_once()
            self.assertTrue(db.await_args.args[0].endswith("import_gmail_reply"))
            self.assertTrue(db.await_args.kwargs["json"]["p_reply"]["opt_out"])

    def test_timeline_orders_inbound_and_outbound_and_never_syncs_on_load(self):
        threads = [{"id":2,"companies":{"name":"IQBAR"},"messages": []},
                   {"id":1,"companies":{"name":"Another company"},"messages": []}]
        def message(id, status, date_field, t):
            return {"id":id,"version":1,"status":status,"subject":f"Subject {id}",
                    "body":"Email text","review_events":[],date_field:t,
                    "inbound_email":{"sender":"demo@example.com","recipients":["sender@gmail.com"]}}
        rows = [message(1,"sent","sent_at","2026-11-21T10:00:00+00:00"),
                message(2,"received","received_at","2026-11-21T11:00:00+00:00"),
                message(3,"draft","created_at","2026-11-21T12:00:00+00:00")]
        with patch.object(main,"signed_in_account",new=AsyncMock(return_value={"role":"reviewer"})), patch.object(
            main,"supabase",new=AsyncMock(side_effect=[httpx.Response(200,json=threads),httpx.Response(200,json=rows),httpx.Response(200,json=[])])
        ), patch.object(main,"sync_known_threads",new=AsyncMock()) as sync:
            page=TestClient(main.app).get("/dashboard/conversations?thread_id=2")
        self.assertEqual(page.status_code,200)
        self.assertEqual(page.text.count('class="conversation-timeline"'),1)
        self.assertLess(page.text.find("Subject 3"),page.text.find("Subject 2"))
        self.assertLess(page.text.find("Subject 2"),page.text.find("Subject 1"))
        self.assertIn("From demo@example.com",page.text)
        self.assertNotIn("Approve and send",page.text)
        sync.assert_not_awaited()
