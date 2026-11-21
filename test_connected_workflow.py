"""Connected FastAPI workflow. Supabase RPCs are simulated, not executed."""
import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import main
from calcom_booking import stamp


class ConnectedWorkflow(unittest.TestCase):
    def test_research_to_booking_with_draft_retry_and_duplicate_sync(self):
        now = datetime.now(timezone.utc)
        slot = stamp((now + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0))
        actor = {"user_id": "reviewer", "role": "admin"}
        target = "tester@example.com"
        sender = "sender@gmail.com"
        quote = "We sell adjustable dumbbells and compact power racks."
        site = "freshfitness.example.com"
        company = {}
        thread = {"id": 7, "status": "ai_handles", "test_recipient": target, "gmail_thread_id": None}
        messages, mailbox, sent, bookings = [], [], [], []
        imports = set()
        reserved = []
        job = {}
        model_attempts = []
        research = dict(name="Fresh Fitness", website=site, score=85, qualification_status="potential_fit",
                        missing_information=[], errors=[], contact=None, reasons=[], score_breakdown={},
                        research_savable=True, research_complete=True, unresolved_criteria=[],
                        overview={"products":{"quote":quote,"source_url":"https://" + site}})

        def add_message(**fields):
            message = {"id": len(messages)+1,"thread_id":7,"version":1,"offered_slots":[],
                       "review_events":[],"created_at":stamp(now),**fields}
            messages.append(message)
            return message

        async def database(path, key, **request):
            data = request.get("json", {})
            params = request.get("params", {})
            name = path.rsplit("/", 1)[-1]
            result = None
            if name == "save_researched_company":
                self.assertFalse(company)
                company.update(data["p_company"], id=32, status="new", contact_email=None, company_contacts=[],
                               company_reviews=[], primary_contact_id=None, reasons=[])
                result = "saved"
            elif name == "companies":
                result = [company] if company else []
            elif name == "record_company_review":
                self.assertEqual(data["p_company"], company["id"])
                company["company_reviews"] = [{"decision":data["p_decision"]}]
                result = 1
            elif name == "reserve_tomba_lookup":
                self.assertEqual(company["company_reviews"][0]["decision"], "demo_fit")
                result = {"status":"reserved","id":1,"domain":site}
            elif name == "finish_tomba_lookup":
                self.assertEqual(data["p_status"], "completed")
                company["company_contacts"] = [dict(contact,id=9) for contact in data["p_contacts"]]
                result = True
            elif name == "start_initial_outreach":
                if data["p_retry"]:
                    self.assertEqual(job["state"], "failed")
                    self.assertIsNone(data["p_primary"])
                else:
                    self.assertEqual(data["p_primary"],company["company_contacts"][0]["id"])
                    company["primary_contact_id"] = data["p_primary"]
                job.update(state="generating")
                result = {"status":"generate","contact_id":company["primary_contact_id"],"token":"initial"}
            elif name == "finish_initial_outreach":
                draft = data["p_draft"]
                if "error" in draft:
                    job.update(state="failed", error=draft["error"])
                else:
                    self.assertEqual(data["p_test_recipient"],target)
                    job.update(state="ready")
                    add_message(direction="out",status="draft",subject=draft["subject"],body=draft["body"],
                                initial_outreach=draft["metadata"])
                result = True
            elif name in ("review_initial_message","review_reply_message"):
                message = messages[data["p_message"]-1]
                if message["status"] != "draft" or message["version"] != data["p_version"]:
                    result = {"status":"stale"}
                else:
                    self.assertEqual((data["p_action"], data["p_recipient"]),("approve",target))
                    message.update(status="approved",approved_version=message["version"],actual_recipient=target)
                    result = {"status":"claimed","message":message,"thread":thread}
                    if name == "review_reply_message":
                        result["incoming"] = messages[message["reply_to_id"]-1]
            elif name == "record_initial_send":
                message = messages[data["p_message"]-1]
                self.assertEqual(message["approved_version"],data["p_version"])
                self.assertEqual(data["p_outcome"],"sent")
                message.update(status="sent",sent_at=stamp(now + timedelta(seconds=len(sent)*10)),
                               gmail_message_id=data["p_gmail_id"])
                thread["gmail_thread_id"] = data["p_gmail_thread"]
                result = True
            elif name == "threads":
                result = [{**thread,"messages":[m for m in messages if m["status"]=="sent"]}] if sent else []
            elif name == "import_gmail_reply":
                reply = data["p_reply"]
                self.assertEqual(data["p_gmail_thread"], thread["gmail_thread_id"])
                if reply["gmail_message_id"] in imports:
                    result = "duplicate"
                else:
                    imports.add(reply["gmail_message_id"])
                    add_message(direction="in",status="received",subject=reply["subject"],body=reply["body"],
                                received_at=reply["received_at"],gmail_message_id=reply["gmail_message_id"],inbound_email=reply)
                    result = "imported"
            elif name == "messages":
                if "gmail_message_id" in params:
                    result = [m for m in messages if "eq."+m.get("gmail_message_id","") == params["gmail_message_id"]]
                elif "id" in params:
                    result = [messages[int(params["id"][3:])-1]]
                elif params.get("status") == "eq.sent":
                    result = [{**m,"threads":{"companies":company}} for m in messages
                              if m["status"]=="sent" and m["offered_slots"]]
                else:
                    result = [m for m in reversed(messages) if m["status"] in {"sent","received"}]
            elif name == "reply_decisions":
                result = []
            elif name == "claim_ai_reply":
                result = ({"status":"generate","company_id":32,"token":"reply"}
                          if thread["status"]=="ai_handles" else {"status":"blocked"})
            elif name in ("finish_ai_reply","finish_scheduling_reply"):
                draft = data["p_result"]
                self.assertIsNotNone(draft, data.get("p_error"))
                if draft["needs_human"]:
                    thread.update(status="needs_human", handoff_reason=draft["reason"])
                if draft["body"]:
                    incoming = messages[data["p_incoming"]-1]
                    add_message(status="draft",direction="out",body=draft["body"],subject=incoming["subject"],
                                reply_to_id=incoming["id"],offered_slots=draft.get("offered_slots",[]))
                result = True
            elif name == "change_conversation_owner":
                if data["p_action"] == "takeover":
                    thread.update(status="human_took_over",owner=data["p_actor"])
                else:
                    self.assertEqual(thread["owner"],data["p_actor"])
                    thread.update(status="ai_handles",owner=None)
                result = True
            elif name == "compose_manual_reply":
                self.assertEqual(thread["owner"],data["p_actor"])
                incoming = messages[data["p_incoming"]-1]
                add_message(status="draft",direction="out",body=data["p_body"],subject=incoming["subject"],
                            reply_to_id=incoming["id"])
                result = True
            elif name == "reserve_calcom_booking":
                self.assertEqual(thread["status"],"ai_handles")
                self.assertEqual(data["p_email"],target)
                self.assertIsNone(data["p_phone"])
                actual_offer = messages[data["p_offer"]-1]
                self.assertEqual(actual_offer["status"],"sent")
                self.assertIn(data["p_slot"], actual_offer["offered_slots"])
                result = not reserved
                if result:
                    reserved.append(data)
            elif name == "record_calcom_booking":
                self.assertEqual(data["p_incoming"],reserved[0]["p_incoming"])
                bookings.append({"id":1,"thread_id":7,"start_time":slot,"status":"booked",
                                 "threads":{"companies":company},"acceptance":{"review_events":[]}})
                result = True
            elif name == "bookings":
                result = bookings
            else:
                self.fail(f"Unexpected database request {path}")
            return httpx.Response(200,json=result)

        real_client = httpx.AsyncClient

        def provider(request):
            host = request.url.host
            if host == "oauth2.googleapis.com":
                return httpx.Response(200,json={"access_token":"mock"})
            if host == "gmail.googleapis.com" and request.method == "POST":
                data = json.loads(request.content)
                email = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(data["raw"]))
                self.assertEqual(email["To"],target)
                if sent:
                    self.assertEqual(data["threadId"],"gmail-thread")
                    self.assertEqual(email["In-Reply-To"],mailbox[-1]["payload"]["headers"][2]["value"])
                number = len(sent)+1
                sent.append(email)
                mailbox.append({"id":f"sent-{number}","threadId":"gmail-thread","labelIds":["SENT"],
                                "payload":{"headers":[{"name":"Message-ID","value":str(email["Message-ID"])}]}})
                return httpx.Response(200,json={"id":f"sent-{number}","threadId":"gmail-thread"})
            if host == "gmail.googleapis.com":
                return httpx.Response(200,json={"id":"gmail-thread","messages":mailbox})
            if host == "api.cal.com" and request.url.path == "/v2/slots":
                return httpx.Response(200,json={"status":"success","data":{"day":[{"start":slot}]}})
            if host == "api.cal.com" and request.url.path == "/v2/bookings":
                data = json.loads(request.content)
                self.assertEqual(data["attendee"]["email"],target)
                self.assertEqual(data["start"],slot)
                self.assertEqual(len(reserved),1)
                return httpx.Response(201,json={"status":"success","data":{"status":"accepted","uid":"mock","start":slot}})
            self.fail(f"Unexpected provider request {host} {request.url.path}")

        def add_inbound(text):
            number = len(mailbox)
            headers = {"From":target,"To":sender,"Message-ID":f"<reply-{number}@example.com>",
                       "Subject":"Re: Fitness bookkeeping",
                       "In-Reply-To":str(sent[-1]["Message-ID"])}
            mailbox.append({"id":f"in-{number}","threadId":"gmail-thread","labelIds":["INBOX"],
                            "internalDate":str(int((now+timedelta(seconds=300+number)).timestamp()*1000)),
                            "payload":{"mimeType":"text/plain","headers":[{"name":k,"value":v} for k,v in headers.items()],
                                       "body":{"data":base64.urlsafe_b64encode(text.encode()).decode()}}})

        def run_research(*keys, on_company, **kwargs):
            on_company(research)
            return SimpleNamespace(stop_reason="",discovery_batches=[],issues=[],searches=0,pages=[],
                                   model_requests=0,model_attempts=[]), [], None, None

        def gemini(model, prompt, schema, kind):
            if "Write an original" in prompt:
                model_attempts.append("first")
                body = ("Hi Sam,\n\nI saw your adjustable dumbbells and\ncompact power racks.\n\n"
                        "Ledgerwise provides monthly bookkeeping for e-commerce brands.\n\n"
                        "Would you be open to discussing how Ledgerwise could help?\n\nBest,\nLedgerwise Team")
                return {"subject":"Fitness bookkeeping","body":body.replace("\n\n", "\n") if len(model_attempts)>1 else "Malformed",
                        "explanation":"Uses sourced products","fact_ids":["overview:products"]}
            if "Analyze the latest" in prompt:
                if "Could we schedule a call?" in prompt:
                    return {"intent":"meeting_interest","action":"scheduling_interest","reason":"Requested a call",
                            "subject":"Re: Fitness bookkeeping","body":"Hi Sam,\n\nHappy to arrange a brief call.\n\nBest,\nLedgerwise Team",
                            "needs_human":False,"fact_ids":[]}
                return {"intent":"service_question","action":"explain_services","reason":"Asked about services",
                        "subject":"Re: Fitness bookkeeping","body":"Hi Sam,\n\nWe provide monthly bookkeeping for e-commerce brands.\n\nBest,\nLedgerwise Team",
                        "needs_human":False,"fact_ids":[]}
            if "Can you tell me more?" in prompt and "Check each factual claim" not in prompt:
                return {"supported":False,"issues":[{"claim":"Example reply claim","reason":"Unsupported in this test"}]}
            return {"supported":True,"personalized":"Check each factual claim" in prompt,"issues":[]}

        env = {"APP_BASE_URL":"http://testserver","TEST_EMAIL":target,"TAVILY_API_KEY":"mock","GEMINI_API_KEY":"mock",
               "TOMBA_API_KEY":"mock","TOMBA_API_SECRET":"mock","GMAIL_SEND_ENABLED":"true","GMAIL_CLIENT_ID":"mock",
               "GMAIL_CLIENT_SECRET":"mock","GMAIL_REFRESH_TOKEN":"mock","GMAIL_SENDER_EMAIL":sender,
               "CALCOM_API_KEY":"mock","CALCOM_EVENT_TYPE_ID":"42","CALCOM_TIME_ZONE":"America/New_York",
               "CALCOM_BOOKING_ENABLED":"true"}
        tomba = {"data":{"emails":[{"email":"sam@"+site,"full_name":"Sam","position":"Founder","score":98,
                                   "verification":{"status":"valid"}}]}}
        with patch.dict(os.environ,env), patch.dict(main.RESEARCH_STATE,{"running":False}), patch.object(
            main,"require_account",new=AsyncMock(return_value=actor)
        ), patch.object(main,"signed_in_account",new=AsyncMock(return_value=actor)), patch.object(
            main,"supabase",side_effect=database
        ), patch.object(main,"run_pilot",side_effect=run_research), patch.object(
            main,"request_tomba",new=AsyncMock(return_value=tomba)
        ) as lookup, patch("research_pilot.Pilot.request_model",autospec=True,side_effect=gemini), patch.object(
            httpx,"AsyncClient",side_effect=lambda *a, **kw: real_client(*a,transport=httpx.MockTransport(provider),**kw)
        ):
            client = TestClient(main.app,follow_redirects=False)
            def post(path, data=None, status=200):
                response = client.post(path,data=data,headers={"origin":"http://testserver"})
                self.assertEqual(response.status_code,status,response.text)
                return response

            post("/companies/research",{"count":1},303)
            self.assertEqual(company["website"],site)
            post("/companies/32/review",{"decision":"demo_fit"},303)
            post("/drafts/companies/32/lookup",status=303)
            post("/drafts/companies/32/contacts",{"primary_id":9},303)
            self.assertEqual(job["state"],"failed")
            self.assertEqual(messages,[])
            post("/drafts/companies/32/retry",status=303)
            lookup.assert_awaited_once()
            self.assertEqual(job["state"],"ready")
            first=messages[-1]
            post(f"/drafts/messages/{first['id']}/approve",{"version":2},409)
            self.assertFalse(sent)
            post(f"/drafts/messages/{first['id']}/approve",{"version":1})
            post(f"/drafts/messages/{first['id']}/approve",{"version":1},409)
            self.assertEqual(len(sent),1)
            add_inbound("What services do you provide?")
            post("/conversations/sync",status=303)
            reply=messages[-1]
            self.assertEqual(reply["status"],"draft")
            self.assertIn("monthly bookkeeping",reply["body"])
            post(f"/conversations/replies/{reply['id']}/approve",{"version":1})
            add_inbound("Thanks. Can you tell me more?")
            post("/conversations/sync",status=303)
            self.assertEqual(thread["status"],"needs_human")
            self.assertIn("Unsupported in this test",thread["handoff_reason"])
            incoming=messages[-1]
            self.assertEqual(incoming["direction"],"in")
            post("/conversations/threads/7/takeover")
            post(f"/conversations/incoming/{incoming['id']}/compose",{"body":"I can check our availability."})
            manual=messages[-1]
            self.assertEqual(manual["status"],"draft")
            post(f"/conversations/replies/{manual['id']}/approve",{"version":1})
            post("/conversations/threads/7/return_to_ai")
            add_inbound("Could we schedule a call?")
            post("/conversations/sync",status=303)
            offer=messages[-1]
            self.assertEqual(offer["offered_slots"][0]["start"],slot)
            post(f"/conversations/replies/{offer['id']}/approve",{"version":1})
            add_inbound("Option 1 works for me. Please book it.")
            post("/conversations/sync",status=303)
            count=len(messages)
            post("/conversations/sync",status=303)
            self.assertEqual(len(messages),count)
            self.assertEqual(len(bookings),1)
            self.assertEqual(len(sent),4)
            self.assertEqual(messages[-1]["status"],"draft")
            self.assertIn("Your call is confirmed",messages[-1]["body"])
            page=client.get("/dashboard/meetings")
            self.assertEqual(page.status_code,200)
            self.assertIn("Fresh Fitness",page.text)
            self.assertIn('data-meeting-status="Upcoming"',page.text)
