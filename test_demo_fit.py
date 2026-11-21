import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

import main
import initial_outreach
from ledgerwise_service import SERVICE_DESCRIPTION


def first_email(opening="I wanted to introduce Ledgerwise.",
                question="Would you be interested in a brief conversation?"):
    return "\n\n".join(("Hi Will,", opening, "Ledgerwise offers monthly bookkeeping for e-commerce brands.",
                       question, "Best,\nLedgerwise Team"))


class DemoFitTests(unittest.TestCase):
    def setUp(self):
        self.account = {"user_id": str(UUID(int=1)), "role": "reviewer"}
        self.client = TestClient(main.app, follow_redirects=False)
        self.headers = {"origin": "http://testserver"}

    def test_review_decisions(self):
        cases = (
            ("demo_fit", 303), ("confirmed_fit", 303), ("rejected", 303),
            ("needs_more_research", 303), ("pending", 422), ("invalid", 422),
        )
        for decision, expected in cases:
            with self.subTest(decision=decision), patch.dict(os.environ, {"APP_BASE_URL": "http://testserver"}), patch.object(
                main, "require_account", new=AsyncMock(return_value=self.account)
            ), patch.object(main, "read_companies", new=AsyncMock(return_value=[{"id": 1}])), patch.object(
                main, "render_company_view", return_value=main.JSONResponse({}, status_code=422)
            ), patch.object(main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=1))) as db:
                response = self.client.post("/companies/1/review", data={"decision": decision}, headers=self.headers)
                self.assertEqual(response.status_code, expected)
                if expected == 303:
                    self.assertEqual(db.await_args.kwargs["json"]["p_decision"], decision)
                else:
                    db.assert_not_awaited()

    def test_drafts_and_confirmed_count(self):
        companies = [{"id": i, "human_status": status, "company_contacts": []} for i, status in enumerate(
            ["confirmed_fit", "demo_fit", "rejected", "needs_more_research", "pending"], start=1
        )]
        with patch.object(main, "signed_in_account", new=AsyncMock(return_value=self.account)), patch.object(
            main, "read_companies", new=AsyncMock(return_value=companies)
        ), patch.object(main, "render", return_value=main.JSONResponse({})) as render, patch.object(
            main, "render_company_view", return_value=main.JSONResponse({})
        ) as overview, patch.object(main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=[]))):
            self.assertEqual(self.client.get("/dashboard/drafts").status_code, 200)
            self.assertEqual(render.call_args.kwargs["eligible_companies"], companies[:2])
            self.assertEqual(self.client.get("/dashboard/overview").status_code, 200)
            self.assertEqual(overview.call_args.kwargs["counts"]["confirmed_fit"], 1)

    def test_only_a_reserved_lookup_reaches_tomba(self):
        for status, expected in (("reserved", 303), ("existing", 303), ("fit_required", 403), ("limit", 409)):
            result = httpx.Response(200, json={"status": status, "id": 1, "domain": "example.com"})
            with self.subTest(status=status), patch.dict(os.environ, {
                "APP_BASE_URL": "http://testserver", "TOMBA_API_KEY": "mock", "TOMBA_API_SECRET": "mock"
            }), patch.object(main, "require_account", new=AsyncMock(return_value=self.account)), patch.object(
                main, "supabase", new=AsyncMock(side_effect=[result, httpx.Response(200)])
            ), patch.object(main, "request_tomba", new=AsyncMock(return_value={})), patch.object(
                main, "parse_tomba_contacts", return_value=[]
            ):
                response = self.client.post("/drafts/companies/1/lookup", headers=self.headers)
                self.assertEqual(response.status_code, expected)
                self.assertEqual(main.request_tomba.await_count, int(status == "reserved"))

    def test_bulk_uses_one_reservation_per_id_and_skips_blocked_domains(self):
        def reservation(status, company_id):
            return httpx.Response(200, json={"status": status, "id": company_id, "domain": f"company{company_id}.com"})

        responses = [
            reservation("existing", 1), reservation("reserved", 2), httpx.Response(200),
            reservation("fit_required", 3), reservation("limit", 4),
        ]
        with patch.dict(os.environ, {"APP_BASE_URL": "http://testserver", "TOMBA_API_KEY": "mock",
                                      "TOMBA_API_SECRET": "mock"}), patch.object(
            main, "require_account", new=AsyncMock(return_value=self.account)
        ), patch.object(main, "supabase", new=AsyncMock(side_effect=responses)) as db, patch.object(
            main, "request_tomba", new=AsyncMock(return_value={"data": {"emails": []}})
        ) as tomba, patch.object(
            main, "email_drafts_page", new=AsyncMock(return_value=main.JSONResponse({}))
        ) as render:
            response = self.client.post("/drafts/companies/lookup",
                data={"company_ids": ["1", "2", "2", "3", "4"]}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            tomba.assert_awaited_once_with("company2.com", "mock", "mock")
            self.assertEqual(db.await_args_list[2].kwargs["json"], {
                "p_lookup": 2, "p_status": "no_results", "p_contacts": [], "p_preferred": None,
            })
            self.assertEqual(render.await_args.kwargs["lookup_results"], {
                "existing": 1, "no_results": 1, "fit_required": 1, "limit": 1,
            })

    def test_bulk_requires_valid_selection_and_authorization(self):
        with patch.dict(os.environ, {"APP_BASE_URL": "http://testserver"}), patch.object(
            main, "require_account", new=AsyncMock(side_effect=main.HTTPException(403))
        ), patch.object(main, "supabase", new=AsyncMock()) as db:
            self.assertEqual(self.client.post("/drafts/companies/lookup", data={"company_ids": "1"},
                                              headers=self.headers).status_code, 403)
            self.assertEqual(self.client.post("/drafts/companies/lookup", data={},
                                              headers=self.headers).status_code, 422)
            db.assert_not_awaited()

    def test_save_distinct_primary_and_backup(self):
        cases = (("10", "11", "saved", 303), ("10", "", "saved", 303),
                 ("10", "10", None, 200), ("", "11", None, 200), ("25", "", "invalid_contact", 200))
        for primary, backup, result, status in cases:
            with self.subTest(primary=primary, backup=backup), patch.dict(
                os.environ, {"APP_BASE_URL": "http://testserver"}
            ), patch.object(main, "require_account", new=AsyncMock(return_value=self.account)), patch.object(
                main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json={"status": result}))
            ) as db, patch.object(main, "email_drafts_page", new=AsyncMock(return_value=main.JSONResponse({}))) as page:
                response = self.client.post("/drafts/companies/1/contacts",
                    data={"primary_id": primary, "backup_id": backup}, headers=self.headers)
                self.assertEqual(response.status_code, status)
                if result:
                    self.assertEqual(db.await_args.kwargs["json"], {
                        "p_company": 1, "p_actor": self.account["user_id"],
                        "p_primary": int(primary), "p_backup": int(backup) if backup else None, "p_retry": False,
                    })
                else:
                    db.assert_not_awaited()
                if status != 303:
                    self.assertTrue(page.await_args.kwargs["contact_error"])

    def test_saved_assignments_render_in_table_two(self):
        person = {"name": "Finance Lead", "role": "CFO", "provider_confidence": 95,
                  "provider_status": "valid", "delivery_status": "verified",
                  "contact_type": "finance"}
        company = {"id": 1, "name": "Example", "website": "example.com", "human_status": "demo_fit",
                   "primary_contact_id": 10, "backup_contact_id": 11, "company_contacts": [
                       {**person, "id": 10, "email": "primary@example.com"},
                       {**person, "id": 11, "email": "backup@example.com"},
                   ]}
        with patch.object(main, "signed_in_account", new=AsyncMock(return_value=self.account)), patch.object(
            main, "read_companies", new=AsyncMock(return_value=[company])
        ), patch.object(main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=[]))), patch.object(
            main, "request_tomba", new=AsyncMock()
        ) as tomba:
            for _ in range(2):
                html = self.client.get("/dashboard/drafts").text
                self.assertIn(">Primary contact</th>", html)
                self.assertIn(">Backup contact</th>", html)
                self.assertIn("Primary validated", html)
                self.assertIn("Save them as shown to start the first email draft", html)
                self.assertNotIn("Generate first draft</button>", html)
                self.assertNotIn("Retry draft</button>", html)
                self.assertEqual(html.count('data-review-dialog="review-contacts-1"'), 1)
                self.assertNotIn('aria-label="Select Example"', html)
            tomba.assert_not_awaited()

    def test_discovered_unverified_contact_moves_before_validation(self):
        contact = {"id": 10, "name": "Will Nitze", "role": "Founder", "email": "will@example.com",
                   "provider_confidence": 85, "provider_status": "unknown", "delivery_status": "unverified"}
        company = {"id": 25, "name": "Example", "website": "example.com", "human_status": "confirmed_fit",
                   "primary_contact_id": None, "backup_contact_id": None, "company_contacts": [contact]}
        with patch.object(main, "signed_in_account", new=AsyncMock(return_value=self.account)), patch.object(
            main, "read_companies", new=AsyncMock(return_value=[company])
        ), patch.object(main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=[]))):
            html = self.client.get("/dashboard/drafts").text
        self.assertIn('data-review-dialog="review-contacts-25"', html)
        self.assertIn("Awaiting validation", html)
        self.assertIn("Delivery unverified", html)
        self.assertNotIn('aria-label="Select Example"', html)

    def test_existing_assignment_can_start_once_and_saves_or_marks_failure(self):
        contact = {"id": 10, "name": "Will Nitze", "role": "Founder", "email": "will@example.com"}
        company = {"id": 25, "name": "Example", "human_status": "confirmed_fit",
                   "primary_contact_id": 10, "company_contacts": [contact], "reasons": []}
        job = {"status": "generate", "token": str(UUID(int=7)), "contact_id": 10}
        for fail in (False, True):
            response_queue = [
                httpx.Response(200, json=job), httpx.Response(200, json=not fail),
                httpx.Response(200, json={"status": "saved"}),
            ]
            with self.subTest(fail=fail), patch.dict(os.environ, {
                "APP_BASE_URL": "http://testserver", "TEST_EMAIL": "test@example.com", "GEMINI_API_KEY": "mock",
            }), patch.object(main, "require_account", new=AsyncMock(return_value=self.account)), patch.object(
                main, "read_companies", new=AsyncMock(return_value=[company])
            ), patch.object(main, "supabase", new=AsyncMock(side_effect=response_queue)) as db, patch.object(
                initial_outreach.Pilot, "request_model",
                side_effect=httpx.ReadTimeout("mock") if fail else [
                    {"subject": "Bookkeeping question", "body": first_email(),
                     "explanation": "No useful saved company fact.", "fact_ids": []},
                    {"supported": True, "personalized": False, "issues": []},
                ],
            ) as model:
                for _ in range(2):
                    response = self.client.post("/drafts/companies/25/contacts",
                        data={"primary_id": "10"}, headers=self.headers)
                    self.assertEqual(response.status_code, 303)
                self.assertEqual(model.call_count, 1 if fail else 2)
                payload = db.await_args_list[1].kwargs["json"]
                self.assertEqual(payload["p_token"], job["token"])
                self.assertEqual(payload["p_test_recipient"], "test@example.com")
                if fail:
                    self.assertIn("timed out", payload["p_draft"]["error"])
                else:
                    self.assertIn("Hi Will,", payload["p_draft"]["body"])
                    self.assertEqual(payload["p_draft"]["metadata"]["contact_id"], 10)
                    self.assertEqual(payload["p_draft"]["facts_used"], [])
                    self.assertEqual(payload["p_draft"]["metadata"]["personalization"], "limited")

    def test_stale_contact_is_not_sent_to_gemini(self):
        company = {"primary_contact_id": 11, "human_status": "confirmed_fit",
                   "company_contacts": [{"id": 10}, {"id": 11}]}
        with patch.dict(os.environ, {"TEST_EMAIL": "test@example.com"}), patch.object(
            main, "read_companies", new=AsyncMock(return_value=[company])
        ), patch.object(main, "generate_initial_draft") as model, patch.object(
            main, "supabase", new=AsyncMock(return_value=httpx.Response(200, json=False))
        ) as db:
            import asyncio
            asyncio.run(main.finish_initial_outreach(25, self.account["user_id"], {"token": "old", "contact_id": 10}))
            model.assert_not_called()
            self.assertIn("primary contact", db.await_args.kwargs["json"]["p_draft"]["error"])

    def test_gemini_cannot_insert_an_unknown_claim_or_price(self):
        company = {"name": "Example", "reasons": [
            {"id": 5, "criterion": "new_channel", "evidence_status": "verified",
             "safe_url": "https://example.com", "quote": "Example products now come at a price of $49."}
        ]}
        contact = {"id": 10, "name": "Will", "role": "Founder", "email": "will@example.com"}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), patch.object(
            initial_outreach.Pilot, "request_model",
            return_value={"subject": "Bookkeeping", "body": "You charge $49.", "explanation": "Used a price.",
                          "fact_ids": ["reason:5"]},
        ), self.assertRaises(ValueError):
            initial_outreach.generate_initial_draft(company, contact, self.account["user_id"])

    def test_iqbar_product_evidence_supports_original_gemini_wording(self):
        # Representative saved research matching the IQBAR details reported in the dashboard.
        evidence = {"source_url": "https://www.eatiqbar.com/", "quote": "Plant Protein Bars 12g Protein | 8-9g Fiber | 1-2g Sugar HYDRATE Hydration Mixes Electrolytes"}
        listing = {"id": 100, "source_url": "https://www.eatiqbar.com/", "evidence_status": "verified",
                   "criterion": "ecommerce", "quote": "Gingerbread (12 Bars) Sale price $24.99"}
        company = {"name": "IQBAR", "reasons": [listing], "research_summary": {"overview": {"products": evidence}}}
        contact = {"id": 10, "name": "Will Nitze", "role": "Founder", "email": "will@example.com"}
        body = first_email("I came across IQBAR's online shop and its range of plant protein bars and hydration mixes.")
        proposed = {"subject": "A bookkeeping question for IQBAR", "body": body,
                    "explanation": "Used the saved product and online store evidence.", "fact_ids": ["overview:products", "reason:100"]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), patch.object(
            initial_outreach.Pilot, "request_model", side_effect=[proposed, {"supported": True, "personalized": True, "issues": []}]
        ) as model:
            draft = initial_outreach.generate_initial_draft(company, contact, self.account["user_id"])
            self.assertEqual(draft["body"], body)
            self.assertEqual(draft["metadata"]["personalization"], "supported")
            self.assertEqual(draft["facts_used"][0]["quote"], evidence["quote"])
            self.assertIn(evidence["quote"], model.call_args.args[0])
            self.assertIn(listing["quote"], model.call_args.args[0])
            for request in model.call_args_list:
                self.assertIn(SERVICE_DESCRIPTION, request.args[0])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), patch.object(
            initial_outreach.Pilot, "request_model",
            side_effect=[{**proposed, "body": first_email("IQBAR is doubling revenue.")}, {"supported": False, "personalized": False, "issues": [{"claim": "doubling revenue", "reason": "Missing revenue evidence", "fact_ids": ["overview:products"]}]}],
        ), self.assertRaisesRegex(ValueError, "doubling revenue.*Missing revenue evidence.*overview:products"):
            initial_outreach.generate_initial_draft(company, contact, self.account["user_id"])

    def test_approved_ledgerwise_facts_need_no_prospect_citations(self):
        from ledgerwise_service import EVIDENCE_RULES, SERVICE_DESCRIPTION
        company = {"name": "Torque Fitness", "reasons": []}
        contact = {"id": 28, "name": "Sam", "role": "Founder", "email": "sam@example.com"}
        draft = {"subject": "Bookkeeping for your team", "body":
                 "Hi Sam,\n\nI'm reaching out to introduce myself.\n\n" + SERVICE_DESCRIPTION
                 + "\n\nWould you be open to a conversation?\n\nBest,\nLedgerwise Team",
                 "explanation": "No supported prospect details are available.", "fact_ids": []}
        for supported, issue in ((True, None), (False, "Torque Fitness is expanding."),
                                 (False, "Ledgerwise also files taxes.")):
            review = {"supported": supported, "personalized": False, "issues": [] if supported else [
                {"claim": issue, "reason": "Unsupported claim", "fact_ids": []}
            ]}
            copy = draft if supported else {**draft, "body": draft["body"].replace(
                "I'm reaching out to introduce myself.", issue)}
            with self.subTest(issue=issue), patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), patch.object(
                initial_outreach.Pilot, "request_model", side_effect=[copy, review]
            ) as model:
                if supported:
                    result = initial_outreach.generate_initial_draft(company, contact, "reviewer")
                    self.assertEqual(result["facts_used"], [])
                    self.assertEqual(result["metadata"]["personalization"], "limited")
                else:
                    with self.assertRaisesRegex(ValueError, "Unsupported claim"):
                        initial_outreach.generate_initial_draft(company, contact, "reviewer")
                for call in model.call_args_list:
                    self.assertIn(EVIDENCE_RULES, call.args[0])
                    self.assertIn(SERVICE_DESCRIPTION, call.args[0])

    def test_torque_first_email_service_sentence_still_passes_evidence_verification(self):
        company = {"name": "Torque Fitness", "reasons": [], "research_summary": {"overview": {
            "products": {"quote": "Shop racks and fitness equipment.", "source_url": "https://www.torquefitness.com/"}}}}
        contact = {"id": 28, "name": "Sam", "role": "Founder", "email": "sam@example.com"}
        service = ("Ledgerwise is a US bookkeeping firm providing monthly bookkeeping for e-commerce brands, "
                   "helping businesses like yours maintain accurate financial records.")
        body = first_email("I came across your racks and fitness equipment.")
        blocks = body.split("\n\n")
        blocks[2] = service
        draft = {"subject": "Bookkeeping for Torque Fitness", "body": "\n\n".join(blocks),
                 "explanation": "Uses a supported product detail.", "fact_ids": ["overview:products"]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), patch.object(
            initial_outreach.Pilot, "request_model", side_effect=[draft, {
                "supported": True, "personalized": True, "issues": [],
            }]
        ):
            result = initial_outreach.generate_initial_draft(company, contact, "reviewer")
        self.assertIn(service, result["body"])
        self.assertEqual(result["metadata"]["personalization"], "supported")

    def test_first_email_blocks_and_newlines(self):
        body = first_email("I came across IQBAR's range of plant protein bars and hydration mixes.")
        self.assertEqual(initial_outreach.format_first_email(body.replace("\n", "\r\n"), "IQBAR"), body)
        self.assertEqual(len(body.split("\n\n")), 5)
        self.assertTrue(body.endswith("Best,\nLedgerwise Team"))
        with self.assertRaisesRegex(ValueError, "separated by blank lines"):
            initial_outreach.format_first_email(" ".join(body.split()), "IQBAR")

    def test_first_email_allows_wrapping_and_natural_discussion_invitation(self):
        draft = first_email("I came across IQBAR today and saw your\n  plant protein bars.",
                            question="Would you be open to discussing how Ledgerwise could help?")
        formatted = initial_outreach.format_first_email(draft, "IQBAR")
        self.assertIn("today and saw your plant protein bars.", formatted)
        self.assertIn("discussing how Ledgerwise could help?", formatted)
        self.assertEqual(len(formatted.split("\n\n")), 5)

    def test_generated_first_email_repairs_only_whitespace_in_realistic_layouts(self):
        standard = first_email("I came across Torque Fitness and saw your two racks.")
        layouts = (
            standard.replace("\n\n", "\n"),
            standard.replace("\n", "\r\n").replace("and saw", "and\r\n  saw"),
            standard.replace("\n\n", "\n").replace("and saw", "and\n  saw"),
            " ".join(standard.split()),
        )
        for body in layouts:
            with self.subTest(body=body):
                normalized = initial_outreach.normalize_generated_first_email(body)
                self.assertEqual(normalized.split(), body.split())
                self.assertEqual(initial_outreach.format_first_email(normalized, "Torque Fitness"), standard)

    def test_generated_first_email_rejects_ambiguous_layout_and_preserves_forbidden_content(self):
        ambiguous = ("Hi Will, Nice work. Our team can help. Would you like to talk? Best, Ledgerwise Team")
        with self.assertRaises(initial_outreach.EmailFormatError):
            initial_outreach.normalize_generated_first_email(ambiguous)
        for question in ("Could we talk next Tuesday?", "Could we talk at https://cal.com/example?"):
            body = " ".join(first_email(question=question).split())
            normalized = initial_outreach.normalize_generated_first_email(body)
            self.assertEqual(normalized.split(), body.split())
            with self.assertRaisesRegex(ValueError, "must not propose specific times"):
                initial_outreach.format_first_email(normalized, "Torque Fitness")

    def test_rejected_first_email_logs_layout_without_body_or_credentials(self):
        body = "Hi Sam, confidential-draft-marker"
        draft = {"subject": "Bookkeeping", "body": body, "explanation": "Introduction", "fact_ids": []}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "secret-key-marker"}), patch.object(
            initial_outreach.Pilot, "request_model", return_value=draft
        ) as model, self.assertLogs("initial_outreach", level="WARNING") as logs:
            with self.assertRaises(initial_outreach.EmailFormatError):
                initial_outreach.generate_initial_draft({"name": "Torque Fitness", "reasons": []},
                    {"name": "Sam", "role": "Founder"}, "reviewer")
            logged = "\n".join(logs.output)
            self.assertIn("stage=format", logged)
            self.assertIn("lines=1", logged)
            self.assertNotIn("confidential-draft-marker", logged)
            self.assertNotIn("secret-key-marker", logged)
            self.assertEqual(model.call_count, 1)

    def test_first_email_accepts_natural_interest_questions_without_a_call_keyword(self):
        for question in ("Would you be open to hearing more?", "Would you be interested in learning more?",
                         "Is this something you'd like to explore?", "Would it be helpful if I shared a little more?"):
            with self.subTest(question=question):
                body = first_email(question=question)
                self.assertEqual(initial_outreach.format_first_email(body, "Simple Sugars"), body)

    def test_first_email_distinguishes_missing_question_from_scheduling(self):
        for ending in ("Let me know if you'd like to hear more.", "Are you interested? Would this help?"):
            with self.subTest(ending=ending), self.assertRaisesRegex(ValueError, "must end with one invitation question"):
                initial_outreach.format_first_email(first_email(question=ending), "Simple Sugars")
        for question in ("Would a call Tuesday at 10 AM work?", "Could we schedule a quick call?",
                         "Could we book a 15-minute intro call?", "Could we talk at https://cal.com/example?"):
            with self.subTest(question=question), self.assertRaisesRegex(ValueError, "must not propose specific times"):
                initial_outreach.format_first_email(first_email(question=question), "Simple Sugars")

    def test_first_email_verifier_still_rejects_unrelated_closing_question(self):
        company = {"name": "Simple Sugars", "reasons": []}
        contact = {"id": 35, "name": "Gina", "role": "Founder", "email": "gina@example.com"}
        question = "Is your office painted blue?"
        draft = {"subject": "Bookkeeping", "body": first_email(question=question),
                 "explanation": "Simple introduction.", "fact_ids": []}
        verdict = {"supported": False, "personalized": False, "issues": [
            {"claim": question, "reason": "Does not invite interest in a conversation.", "fact_ids": []}
        ]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), patch.object(
            initial_outreach.Pilot, "request_model", side_effect=[draft, verdict]
        ) as model:
            with self.assertRaisesRegex(ValueError, "Does not invite interest"):
                initial_outreach.generate_initial_draft(company, contact, "reviewer")
            self.assertEqual(model.call_count, 2)
            self.assertIn("Verify the final question only asks", model.call_args.args[0])

    def test_first_email_rejects_scheduling_and_awkward_brand_wording(self):
        invalid = (
            first_email("I came across your IQBAR plant protein bars."),
            first_email(question="Would you be interested in a call on Tuesday?"),
            first_email(question="Would you be interested in a call at 10:30?"),
            first_email(question="Can you book a call at https://cal.com/example?"),
            first_email("Your meeting is scheduled."),
            first_email("I\u2019m available on Tuesday."),
            first_email("Tuesday at 10:30 suits us."),
            first_email(question="Would you be interested in Monday or Wednesday's available slots?"),
        )
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                initial_outreach.format_first_email(body, "IQBAR")

    def test_contradictory_verification_blocks_and_logs_the_verdict(self):
        quote = "Plant Protein Bars HYDRATE Hydration Mixes ENERGIZE Mushroom Coffees"
        company = {"name": "IQBAR", "reasons": [], "research_summary": {
            "overview": {"products": {"quote": quote, "source_url": "https://www.eatiqbar.com/"}}
        }}
        contact = {"id": 10, "name": "Will", "role": "Founder", "email": "will@example.com"}
        claim = "I came across your plant protein bars, hydration mixes, and mushroom coffees."
        draft = {"subject": "Bookkeeping question", "body": first_email(claim), "explanation": "Product detail",
                 "fact_ids": ["overview:products"]}
        observation = {"claim": claim, "reason": "The evidence correctly supports the product lines.",
                       "fact_ids": ["overview:products"]}
        cases = [(True, True, [observation]), (False, True, []), (True, False, [])]
        for supported, personalized, issues in cases:
            with self.subTest(supported=supported, personalized=personalized), patch.dict(
                os.environ, {"GEMINI_API_KEY": "mock"}
            ), patch.object(initial_outreach.Pilot, "request_model", side_effect=[draft, {
                "supported": supported, "personalized": personalized, "issues": issues,
            }]) as model, self.assertLogs("initial_outreach", level="WARNING") as logs:
                with self.assertRaisesRegex(ValueError, "Inconsistent verifier response"):
                    initial_outreach.generate_initial_draft(company, contact, self.account["user_id"])
                self.assertIn("issues=[]", model.call_args.args[0])
                self.assertIn(f"supported={supported}", logs.output[0])
                self.assertIn(f"personalized={personalized}", logs.output[0])
                self.assertIn("citation_count=1", logs.output[0])
                self.assertNotIn("overview:products", logs.output[0])
                self.assertNotIn(claim, logs.output[0])

    def test_refresh_requests_replacement_using_original_draft_id(self):
        with patch.dict(os.environ, {"APP_BASE_URL": "http://testserver"}), patch.object(
            main, "require_account", new=AsyncMock(return_value=self.account)
        ), patch.object(main, "supabase", new=AsyncMock(return_value=httpx.Response(
            200, json={"status": "generate", "contact_id": 10, "token": "example-token"}
        ))) as db, patch.object(main, "finish_initial_outreach", new=AsyncMock()) as finish:
            response = self.client.post("/drafts/companies/25/refresh-personalization",
                                        data={"message_id": 42}, headers=self.headers)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(db.await_args.kwargs["json"]["p_message"], 42)
            finish.assert_awaited_once()

    def test_refresh_state_and_history_render_in_draft_modal(self):
        from datetime import datetime, timedelta, timezone

        contact = {"id": 16, "name": "Will", "role": "Founder", "email": "will@example.com",
                   "provider_confidence": None, "delivery_status": "unverified"}
        company = {"id": 25, "name": "IQBAR", "website": "example.com",
                   "human_status": "confirmed_fit", "primary_contact_id": 16,
                   "company_contacts": [contact]}
        original = {"id": 1, "thread_id": 1, "version": 1, "status": "draft",
                    "subject": "Original subject", "body": "Original body", "facts_used": [],
                    "initial_outreach": {"contact_id": 16, "name": "Will"},
                    "threads": {"company_id": 25, "demo_only": False}}
        replacement = {**original, "id": 2, "version": 2, "subject": "New subject", "body": first_email("New body"),
                       "initial_outreach": {"contact_id": 16, "personalization": "supported"},
                       "facts_used": [{"quote": "Saved product evidence", "url": "https://example.com"}]}
        for state, age, expected in (("failed", 0, "Retry personalization"),
                                     ("generating", 4, "Retry personalization"),
                                     ("generating", 0, "Regenerating"),
                                     ("ready", 0, "Version 3 of 3")):
            middle = {**replacement, "status": "rejected", "subject": "Middle subject"}
            newest = {**replacement, "id": 3, "version": 3}
            rows = [middle, {**original, "status": "rejected"}, newest] if state == "ready" else [original]
            job = {"company_id": 25, "contact_id": 16, "state": state,
                   "started_at": (datetime.now(timezone.utc) - timedelta(minutes=age)).isoformat(),
                   "failure_reason": "Gemini timed out. Retry when ready." if state == "failed" else None}
            reads = [httpx.Response(200, json=payload) for payload in ([], rows, [job])]
            with self.subTest(state=state, age=age), patch.object(
                main, "signed_in_account", new=AsyncMock(return_value=self.account)
            ), patch.object(main, "read_companies", new=AsyncMock(return_value=[company])), patch.object(
                main, "supabase", new=AsyncMock(side_effect=reads)
            ), patch.object(main, "generate_initial_draft") as model:
                response = self.client.get("/dashboard/drafts")
                self.assertEqual(response.status_code, 200)
                self.assertIn(expected, response.text)
                self.assertIn("Version 1", response.text)
                self.assertIn("Original body", response.text)
                self.assertNotIn("Retry draft</button>", response.text)
                model.assert_not_called()
                if state == "ready":
                    self.assertIn("<pre>" + replacement["body"] + "</pre>", response.text)
                    self.assertEqual(response.text.count('data-review-dialog="review-email-1"'), 1)
                    self.assertIn('data-draft-version="1" hidden', response.text)
                    self.assertIn('data-draft-version="2" hidden', response.text)
                    self.assertIn('data-draft-version="3" >', response.text)
                    self.assertEqual(response.text.count("Approve and send"), 1)
                    self.assertIn('name="message_id" value="3"', response.text)
                    self.assertNotIn('name="message_id" value="2"', response.text)
                    for text in ("New subject", "New body", "Saved product evidence", "Rejected"):
                        self.assertIn(text, response.text)
                if state == "failed":
                    self.assertIn(job["failure_reason"], response.text)

    def test_first_draft_status_and_retry_belong_to_table_three(self):
        contact = {"id": 16, "name": "Reviewer choice", "role": "Founder", "email": "contact@example.com",
                   "provider_confidence": None, "delivery_status": "unverified"}
        company = {"id": 25, "name": "IQBAR", "website": "example.com", "human_status": "confirmed_fit",
                   "primary_contact_id": 16, "company_contacts": [contact]}
        from datetime import datetime, timezone
        for state in ("generating", "failed"):
            job = {"company_id": 25, "contact_id": 16, "state": state,
                   "started_at": datetime.now(timezone.utc).isoformat(), "failure_reason": "Draft timed out."}
            reads = [httpx.Response(200, json=payload) for payload in ([], [], [job])]
            with patch.object(main, "signed_in_account", new=AsyncMock(return_value=self.account)), patch.object(
                main, "read_companies", new=AsyncMock(return_value=[company])
            ), patch.object(main, "supabase", new=AsyncMock(side_effect=reads)):
                response = self.client.get("/dashboard/drafts")
            self.assertEqual(response.status_code, 200)
            contacts_html, drafts_html = response.text.split("AI Email Drafts</h2>", 1)
            self.assertNotIn("Retry draft", contacts_html)
            self.assertNotIn("Review email", drafts_html)
            if state == "generating":
                self.assertIn('data-draft-status="Generating"', drafts_html)
                self.assertIn('class="working-dots"', drafts_html)
            else:
                self.assertIn('data-draft-status="Generation failed"', drafts_html)
                self.assertIn("Draft timed out.", drafts_html)
                self.assertIn("Retry draft</button>", drafts_html)

    def test_demo_recipient_requires_current_approval_and_one_test_inbox(self):
        thread = {"id": 1, "demo_only": True, "test_recipient": "real@company.com"}
        message = {"thread_id": 1, "direction": "out", "status": "approved", "version": 2, "approved_version": 2}
        with patch.dict(os.environ, {"TEST_EMAIL": "test@example.com"}):
            self.assertEqual(main.approved_demo_recipient(thread, message), "test@example.com")
            for change in ({"approved_version": 1}, {"status": "draft"}, {"thread_id": 2}, {"direction": "in"}):
                with self.assertRaises(ValueError):
                    main.approved_demo_recipient(thread, {**message, **change})
        for invalid in ("", "test@example.com,real@company.com"):
            with patch.dict(os.environ, {"TEST_EMAIL": invalid}), self.assertRaises(ValueError):
                main.approved_demo_recipient(thread, message)
