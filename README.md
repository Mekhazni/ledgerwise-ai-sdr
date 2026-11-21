# Ledgerwise AI SDR

Ledgerwise is a sales outreach case study for a fictional US bookkeeping firm that provides monthly bookkeeping for e-commerce brands. It researches prospects, records qualification evidence, drafts emails for review, imports Gmail replies, and schedules calls through Cal.com. The current delivery path is test-only.

## Stack

- Python, FastAPI, Uvicorn, and Jinja2
- Plain HTML, CSS, and JavaScript
- Supabase Postgres and Supabase Auth
- Tavily, HTTPX, and Beautiful Soup for research, with Gemini 3.1 Flash-Lite as the primary model and an optional Groq fallback for research
- Tomba for manual contact lookup, Gmail API for email, and Cal.com API for availability and booking

## Current features

- **Research and qualification.** An admin can discover companies or enter domains manually, up to 10 per batch. Research saves source links, quotes, scores, and gaps. Reviewers can confirm fit, reject a company, request more research, or mark it Demo fit. Demo fit is separate from genuine qualification.
- **Contacts and first emails.** Reviewers can request Tomba lookups for eligible companies, choose a primary and optional backup contact, and review source-grounded first-email drafts. Drafts support version history, editing, rejection, regeneration, and exact-version approval.
- **Conversations.** A manual inbox sync imports replies from known Gmail threads. The app can draft AI replies, disclose AI assistance, answer standalone pricing questions with the approved wording, and flag cases for a reviewer. An assigned reviewer can take over, draft a manual response, and explicitly return control to AI when eligible.
- **Scheduling and meetings.** AI-assisted and reviewer scheduling use Cal.com availability. Slot offers require approval before sending. A booking requires explicit acceptance of a previously sent slot, a fresh availability check, and a reservation. Phone is optional. Confirmed meetings appear in Conversations and Meetings, and the confirmation email remains a draft until approved.
- **Access control.** Supabase Auth handles sign-in. The backend checks reviewer roles. Admin settings support employee invitations, account updates, deactivation, deletion, and password-reset links.

## Run locally

1. Set up a Supabase project and apply the [database setup](#database-setup) below. In Supabase Auth, create the first user, then add an active row to `public.reviewers` with that user's ID, full name, and the `admin` role.
2. Create a virtual environment, install dependencies, and copy the environment template. From PowerShell at the repository root:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   ```

3. Fill in your local `.env`. The dashboard needs the Supabase settings. Configure provider credentials for each workflow you intend to test. For employee invitation and reset links, allow the app's `/account/accept` URL in Supabase Auth. For Gmail, use a dedicated sender and OAuth credentials with send and read-only access. For Cal.com, use a configured event type and timezone.
4. Start FastAPI and open [the local login page](http://127.0.0.1:8000/login).

   ```powershell
   python -m uvicorn main:app --reload
   ```

5. Run the automated suite with `python -m unittest discover -q`. The connected workflow tests mock external providers and Supabase RPC responses. They do not execute the SQL functions on a live database.

## Environment variables

| Purpose | Names |
|---|---|
| Supabase and app | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `APP_BASE_URL` |
| Research and AI | `TAVILY_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY` |
| Contact lookup | `TOMBA_API_KEY`, `TOMBA_API_SECRET` |
| Gmail and test recipient | `TEST_EMAIL`, `GMAIL_SEND_ENABLED`, `GMAIL_SENDER_EMAIL`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` |
| Cal.com | `CALCOM_API_KEY`, `CALCOM_EVENT_TYPE_ID`, `CALCOM_TIME_ZONE`, `CALCOM_BOOKING_ENABLED` |

Keep credentials in the local `.env` or the host's secret settings.

## Database setup

Apply the following files in Supabase SQL Editor on a fresh project. Later files replace earlier functions, so order matters.

1. `schema.sql`
2. `add_reviewers.sql`
3. `secure_employee_access.sql`
4. `manage_employee_lifecycle.sql`
5. `add_company_research.sql`
6. `add_company_reviews.sql`
7. `simplify_reviews_and_save_contacts.sql`
8. `add_research_summary.sql`
9. `add_company_contacts.sql`
10. `add_tomba_contact_lookup.sql`
11. `allow_partial_company_research.sql`
12. `add_demo_fit.sql`
13. `add_outreach_contact_assignments.sql`
14. `add_initial_outreach_drafts.sql`
15. `refresh_initial_personalization.sql`
16. `fix_initial_draft_refresh.sql`
17. `allow_initial_draft_regeneration.sql`
18. `add_outreach_review_and_send.sql`
19. `add_gmail_inbox_sync.sql`
20. `add_ai_reply_drafts.sql`
21. `fix_gmail_reply_import.sql`
22. `add_reviewer_handoff_retry.sql`
23. `add_human_takeover.sql`
24. `add_calcom_booking.sql`
25. `allow_optional_booking_phone.sql`
26. `add_reviewer_scheduling.sql`

`add_human_takeover.sql` and `add_calcom_booking.sql` can switch places. Both need `add_ai_reply_drafts.sql`, and both must precede `add_reviewer_scheduling.sql`. The optional `seed_company.sql` only needs `schema.sql` and inserts a sample company.

## Test mode

- The saved business address identifies the intended contact. The server sends Gmail messages only to `TEST_EMAIL` after a reviewer approves the exact draft version and `GMAIL_SEND_ENABLED` is enabled. Keep the test inbox separate from the Gmail sender.
- Inbox sync is manual. It matches replies to known Gmail threads and their test recipients. Demo fit allows workflow testing while keeping those companies separate from genuine qualification.
- Booking creation requires `CALCOM_BOOKING_ENABLED`. Cal.com uses `TEST_EMAIL` as the attendee. Ledgerwise drafts the confirmation email for approval. Cal.com can send its own notification separately, so check the test event's settings before enabling bookings.

## Client rules and enforcement

| Rule | Where the code enforces it |
|---|---|
| Never quote a price | `initial_outreach.py` checks first drafts for pricing terms. `outreach_delivery.py:verify_edited_copy()` checks edits to first emails. `ai_reply.py:generate_reply()` uses approved wording for standalone pricing, and `verified_reply_copy()` checks other AI replies for prices and unsupported promises. **Limit:** Manual replies rely on reviewer judgment for content. |
| Ground factual claims in saved evidence | `initial_outreach.py:saved_facts()` supplies stored URLs and quotes. `generate_initial_draft()` and `ai_reply.py:verified_reply_copy()` check AI copy against that evidence and the approved facts in `ledgerwise_service.py`. The standalone pricing reply uses approved fixed wording. **Limit:** Manual replies are not fact-checked by the app. |
| Stop outreach after an opt-out | `gmail_inbox.py:clear_opt_out()` detects clear requests. `fix_gmail_reply_import.sql:import_gmail_reply()` records suppression and marks the company opted out. The approval functions in `add_outreach_review_and_send.sql` and `add_human_takeover.sql` enforce suppression before sending. **Limit:** A possible opt-out can remain a human-review flag without a suppression entry. |
| Disclose AI assistance when asked | `ai_reply.py:identity_question_only()` recognizes standalone identity questions. `generate_reply()` returns a disclosure, and `verified_reply_copy()` checks disclosure wording when an AI draft answers a detected question. |
| Hand uncertain cases to a reviewer | `main.py:run_ai_reply()` hands off terminal validation failures, and `add_ai_reply_drafts.sql:finish_ai_reply()` records the handoff reason. `add_human_takeover.sql` enforces ownership and manual reply rules. Temporary provider failures remain retryable. |

## Project layout

| Path | Purpose |
|---|---|
| `main.py` | FastAPI routes, authentication, dashboard actions, and provider orchestration |
| `research_pilot.py` | Discovery, source reading, scoring, and research budgets |
| `initial_outreach.py`, `ledgerwise_service.py` | First-email generation and evidence checks, approved Ledgerwise facts and policies |
| `ai_reply.py` | AI reply decisions, verification, one correction attempt, and standalone pricing replies |
| `gmail_inbox.py`, `outreach_delivery.py` | Inbox parsing and sync, first-email edit verification, and test-only delivery |
| `calcom_booking.py` | Slot retrieval, scheduling interpretation, and Cal.com API calls |
| `templates/`, `static/` | Dashboard pages, dialogs, calendar, and styles |
| `schema.sql`, migration files, `test_*.py` | Database setup and automated tests |

## Known limits

- Research depends on public sources and provider quotas. Revenue and headcount can remain unknown. The application does not guarantee 30 qualified leads.
- Gemini can produce unsupported or poorly formatted copy. Automated tests exercise mocked responses and do not guarantee consistent live model output.
- Clear opt-outs can suppress the shared test inbox as well as the original contact. Ambiguous opt-outs can stop at Needs Human. **TODO:** Implement and verify the proposed reviewer-controlled, contact-only suppression workflow.
- Booking reservations prevent duplicates within one conversation. Simultaneous requests from separate conversations for the same slot rely on Cal.com to reject the conflict. Cancellations made directly in Cal.com are not imported into Meetings.
- **TODO:** Verify fresh database setup against a separate Supabase project and confirm the public deployment URL and two-week availability.
