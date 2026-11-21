> **Living project history | Updated 21 November 2026**  
> **Purpose:** Preserve my reasoning, questions, decisions, implementation chronology, collaboration with ChatGPT/Claude/Codex, problems and resolutions, verified tests, and unfinished work.  
> **Evidence labels:** **Implemented** = reported in the working application or Codex's changes; **Live verified** = observed in real Gmail/Gemini/Cal.com tests; **Mock tested** = tested with simulated providers, not equivalent to a live integration test; **Proposed / rejected / postponed** = not implemented or not confirmed.  
> **Accuracy note:** This consolidates the original notes, the available conversation history, and the results I reported from Codex. It is not a literal archive of every prompt, line of SQL, unseen Codex edit, or unrecorded Claude exchange. Where the exact outcome or implementation is not visible, I say so instead of inventing it.

# Project notes — Ledgerwise AI SDR

## Plan changes

- Planned Google Calendar, switched to Cal.com because the Google login can expire after 7 days and the site must stay up 2 weeks.

- Moved login and deployment after the research test on 3 companies, because research is the riskiest part.

- Simplified the database from 10 tables to 6, so I can explain every column.

- Limited `bookings.status` to `booked`/`cancelled`. No booking row means no call yet.

- After several research tests, decided to stop repeatedly improving the research script and prioritize building the complete platform. The research pipeline works, but it still needs better company qualification.

- Decided to build the platform in checkpoints: (1) authentication, (2) employee management, (3) AI research connected to Supabase, followed by emails, conversations, booking, and deployment.

- Changed authentication from one authorized reviewer ID in `.env` to a `reviewers` database table supporting multiple employees.

- Changed the plan from manually inserting a company to automatically populating Supabase when the AI research feature runs.

- Decided to retain the existing research pipeline and compare Gemini against at least one alternative LLM later, rather than immediately replacing it.

- Kept Cal.com as the booking solution after reconsidering Google Calendar. The seven-day Google OAuth limitation applies to external apps in Testing mode, not every Google Calendar integration.

- Added a separate employee-access security improvement before implementing employee management.

## Where Codex got something wrong or went too fast

- Started writing login and deployment files before the tables and the research test. I declined and set the order.

- Its first schema had no permission for the backend. I noticed because I had unticked the Supabase grant box, and Codex confirmed it.

- The first research script crashed with an `AttributeError` because some Gemini output wasn't properly validated. Codex added validation.

- Gemini returned HTTP 503 errors during research. Codex added limited retries so temporary failures wouldn't stop the whole run.

- The script encountered many HTTP 403 errors when reading websites. We tested Tavily Basic Extract separately on Lulus, and it successfully retrieved 5,884 characters. We then added it as a fallback.

- Some early research findings passed quote checks even though the quotes didn't actually prove the claims. A matching quote is not necessarily valid evidence.

- The research initially treated a press-release dateline as proof of headquarters and quarterly revenue as if it could establish annual revenue.

- The research initially selected the first three discovered companies instead of screening candidates for likely profile fit.

- Richpanel incorrectly received online-sales points despite selling software to e-commerce businesses rather than being the type of product brand Ledgerwise targets.

- Pique initially received zero points even though its storefront quote matched, because Gemini marked the fit as `unclear`.

- The scoring logic didn't recognize that “over 120 employees” was enough to rule out the target of 5–50 employees.

- Codex initially restricted access using `SUPABASE_REVIEWER_ID` in `.env`. I questioned why we needed to manually add every user's ID and requested multi-user authentication.

- The first live login test returned `"Submit the form from Ledgerwise."` Codex identified a likely origin/referrer-policy mismatch and changed `Referrer-Policy` from `no-referrer` to `same-origin`. Login then worked.

- Codex created `seed_company.sql` to add All In Food manually for dashboard testing. I declined because I wanted companies to be inserted automatically through AI research.

- Codex initially suggested testing the Companies page with manually seeded data before implementing the research button. I decided to leave Companies empty until actual research saves results.

- I asked Codex not to create new files or change the database without explaining the changes and getting my approval first.

## Decisions I made

- Every outgoing email needs human approval, not only the first one.

- For the case study, all outgoing emails go only to test addresses I control.

- ****Test Mode / Live Mode:**

  - Research stores real company information and contact emails in `companies`.

  - In Test Mode, FastAPI automatically copies my configured `TEST_EMAIL` into `threads.test_recipient` when creating a conversation.

  - Emails go to my test address, and I reply as if I were the prospect to test the AI.

  - In a future Live Mode, emails would go to the real `companies.contact_email`, with human approval and safety checks.

  - Only Test Mode will be enabled for the Unscale submission.

- ****Gmail thread ID:**

  - `gmail_thread_id` is the unique ID Gmail assigns to an email conversation.

  - After sending the first email, FastAPI saves this ID in `threads.gmail_thread_id`.

  - When a reply arrives, FastAPI uses the ID to match it to the correct company and conversation.

  - `threads.id` identifies the conversation in our database; `gmail_thread_id` identifies it in Gmail.

  - Gmail generates this automatically; I don't enter it manually.

- Revenue/headcount not published = labeled estimate, never a fact.

- A cancelled booking sets the company back to `replied`, unless it has another active booking or it opted out (`opted_out` always wins).

- Before retrying a booking after a timeout, the backend checks Cal.com for the original booking (Codex's suggestion, to avoid booking twice).

- The backend key (`service_role`) skips RLS, so FastAPI must check the reviewer's login before using it.

### Research decisions

- Keep every company that enters research in the JSON, even if it doesn't qualify or its research fails.

- Show qualification score, score breakdown, sources, quotes, missing information, and errors.

- Use a 100-point scoring system:

  - US-based: 25 points.

  - Sells its own products online: 25 points.

  - Annual revenue $1M–$10M: 15 points.

  - Headcount 5–50: 15 points.

  - Recent hiring, funding, or new sales channel: 20 points.

- Show profile fit (80 points) and outreach timing (20 points) separately.

- A sourced estimate within the revenue or headcount range earns 10 instead of 15 points.

- Multiple quotes can support one criterion without increasing its maximum points.

- Distinguish `poor_fit` from `needs_more_research`. Missing information doesn't automatically mean a company doesn't fit.

- Keep companies that fit the profile but have no recent outreach signal.

- Track contact information separately from qualification scoring.

- The research pilot limits were 15 Tavily searches, 30 distinct webpage URLs, 10 Gemini requests, and one Tavily Basic Extract fallback per company.

- The latest reviewed three-company pilot researched Grüns, Cotto, and All In Food, but produced zero confirmed profile fits.

- Unscale requires at least 30 companies that actually match Ledgerwise's profile, not just 30 researched companies.

- Decided not to claim the research limitation is unavoidable before evaluating it. We plan one controlled comparison between Gemini and another LLM, using the same sources and evaluation criteria.

- After connecting research to Supabase, start collecting companies in batches while building emails and booking, rather than waiting until the whole platform is finished.

### Platform and dashboard decisions

- Use FastAPI for the backend, HTML/CSS/JavaScript for the frontend, and Supabase for database and authentication.

- Keep six sidebar sections: Overview, Companies, Email drafts, Conversations, Meetings, and Settings.

- Overview should be a work queue. Clicking a metric opens the corresponding filtered records.

- Companies should display scores, statuses, evidence, source links, missing information, and contacts.

- Email drafts must support Edit, Approve, Reject, and Send Approved Email. Editing clears previous approval.

- Conversations should display full email history, human-handoff reasons, and a Take Over action.

- The dashboard should be professional, simple, and understandable for nontechnical employees.

- Research should be started manually from the Companies page by an admin, not continuously in the background.

- The admin should choose how many companies to research. The initial connected test will use three.

- Research results should automatically save to Supabase and become visible in Companies.

- No AI research action should automatically send an email.

### Authentication and employee management decisions

- Asked what a reviewer account was and learned that it is a Supabase Auth user authorized to access Ledgerwise, not my Supabase project administration account.

- Initially used `SUPABASE_REVIEWER_ID` for one reviewer. Replaced that approach with `public.reviewers` containing the Auth user ID, full name, optional phone, role, and active status.

- Start by creating the first admin directly in Supabase Auth and assigning the admin role in `reviewers`.

- Test with one admin and two reviewers.

- Only admins should create or invite employees and manage their roles or access.

- No public signup page.

- Employees can have an existing personal or professional email address; Ledgerwise does not create email inboxes.

- The admin should invite employees by email. Each employee receives a secure link to set their own password.

- The admin can see employee name, email, optional phone, role, and status, but never their password.

- Passwords are securely hashed and managed by Supabase Auth, not stored in the Ledgerwise employee table.

- Show each employee's full name and role together in the sidebar.

- Settings contains ****My Account**** for admins and reviewers, showing name, email, role, and a Change Password action.

- Settings also contains ****Employees**** for admins only.

- Employee information stays read-only until the admin clicks Edit. Save applies changes, Cancel discards them, and deactivation requires confirmation.

- Protect the last active admin from accidental deactivation or demotion.

- Employees can change their passwords whenever they want while signed in.

- For forgotten passwords, the employee contacts the admin by phone, email, or in person. The admin verifies their identity and sends a reset link.

- I initially considered making every password reset require admin approval through a platform queue. We dropped this because Supabase's normal recovery endpoint could bypass the queue without additional customization.

- The admin-contact reset procedure is our operational rule, not a technically enforced restriction yet.

- Invitation and reset links should expire after one hour, subject to the Supabase email OTP expiration setting.

- Supabase invitation/reset email delivery is separate from the Gmail integration used for sales outreach.

### Security and API decisions

- Use `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and `SUPABASE_SECRET_KEY` for the current HTTPS-based Supabase integration.

- `DATABASE_URL` is not required for Ledgerwise's current architecture. My earlier RAG project used it because that project connected directly to PostgreSQL.

- `APP_BASE_URL` identifies the Ledgerwise application address for request-origin checks and invitation/reset redirects.

- Locally, `APP_BASE_URL=http://127.0.0.1:8000`. After deployment, it becomes the real HTTPS URL.

- Keep API keys in `.env`, never in frontend code or the GitHub repository.

- FastAPI must verify identity, active access, and role before protected operations.

- Knowing the Supabase project URL or publishable key does not grant privileged database access.

- Decided employees should access Ledgerwise application data through FastAPI rather than directly through Supabase's Data API.

- Codex proposed revoking browser-role access to `reviewers`, keeping RLS enabled, and verifying access grants for the sales tables.

- Admin-only actions must be protected in FastAPI, not only hidden from reviewers in the interface.

- Strengthen origin/CSRF protection when adding operations that change data.

- Keep Supabase Auth and the Data API enabled because the backend currently depends on them.

- Before enabling privileged employee management, verify that the live database permissions actually match the intended SQL policies.

- The proposed security and employee-management implementation includes `secure_employee_access.sql` and `templates/account.html`; these changes must be tested against the live project before being considered complete.

### Historical checkpoint: early progress and next steps (recorded before email/booking implementation)

- The research pilot is working technically, but finding 30 confirmed fitting companies remains unresolved.

- The first dashboard layout and six sidebar sections have been created.

- The role-based authentication checkpoint was tested successfully with real Supabase accounts.

- Login, logout, access denial, and role revocation checks worked.

- The Companies page is intentionally empty until research is connected. We will not run `seed_company.sql`.

- Employee management and stronger database-access restrictions are the next checkpoint.

- Then connect the Start Research button to the existing Python pipeline and automatically save results in Supabase.

- Test the research feature with three companies before scaling up.

- Compare Gemini against at least one other model.

- Implement email drafting, approval, Gmail sending, AI replies, opt-outs, and human takeover.

- Integrate Cal.com booking.

- Finish deployment, test the full workflow, and prepare the public GitHub repository, unedited AI coding sessions, `HOW_I_WORKED.md`, and Loom demo.

---

# Expanded chronological project history (18–21 November 2026)

## 0. Assignment, constraints, and my working method

- **Project:** Ledgerwise AI SDR platform for the Unscale AI Engineer take-home assignment. Ledgerwise is a fictional **US bookkeeping firm offering monthly bookkeeping to e-commerce brands**. I treated the assignment's facts as the source of truth, rather than inventing offerings for a nonexistent company.
- **Ideal prospect:** US-based e-commerce brand selling its own products, estimated annual revenue **$1M–$10M**, team **5–50**, preferably a recent signal (hiring, fundraising, new sales channel). Research target: **30 genuinely confirmed profile fits**, not simply 30 rows or guessed matches. Scoring included the 20-point outreach timing component.
- **Time pressure:** Project work began around the evening of **18 November 2026**. The assignment deadline and two-week availability requirements strongly influenced my choices. During the final night, I worked into the morning of **21 November** and prioritized finishing and sleeping rather than chasing perfection.
- **My workflow with AI tools:** I was the product owner and decision maker. I brought the problem statements, proposed ideas, screenshots, test messages, implementation concerns, and trade-offs. I used **ChatGPT** as a thinking partner to break down requirements, explain technical concepts, critique possible choices, help formulate tests, and turn my messy ideas/questions into concise Codex messages with precise acceptance criteria and "do not touch" boundaries. I also consulted **Claude** on planning and Git/project matters; the precise text of every Claude exchange is not available here, so I will not attribute specific implementation decisions to Claude without evidence. **Codex** in VS Code inspected and edited the project, investigated saved records and code paths, proposed implementations, ran tests, and reported results. My acceptance or rejection of Codex's proposals remained an explicit project step.
- **Why I wanted clean Codex messages:** Open-ended conversational messages about a bug could make an implementation agent misunderstand the scope and edit unrelated features. I repeatedly asked ChatGPT to transform my intent into clear requirements, tests, constraints, expected behavior and approval gates. Over time, I became stricter: **investigate and report root cause first; do not edit until approved; do not change already-working booking or first emails; show exactly which files/functions changed; distinguish mocked tests from live behavior**.
- **ChatGPT's useful role:** Simplifying scoring, auth, Supabase SQL/API concepts, Gmail thread IDs, contact discovery, approval-versioning, Cal.com slots, error interpretation, and sequencing tests; explaining why evidence verification may reject unsupported business claims; drafting targeted Codex instructions; reviewing screenshots and proposed AI emails; helping triage between a proper fix and a safe human fallback; and later recommending project freeze, responsible limitation disclosure and submission priorities.
- **Important reflection on the assistance:** Some earlier ChatGPT recommendations confidently labeled narrow changes "safe" or "the last fix", despite incomplete code visibility. Mocked regressions repeatedly passed while real Gemini still failed. I asked for shorter, more practical guidance and increasingly required live replay and a read-only diagnosis first. The better later method was **inspect the exact saved message → trace route/validator → propose minimal change → preserve working code → run regression → verify real Gemini with draft-only replay**.

## 1. Earliest architecture and scope questions

- I first reasoned about **what an SDR does**, what a bookkeeping firm for e-commerce brands means, what the ICP constraints mean, and how to turn a broad assignment into a workflow I could actually build.
- I considered how to source companies from arbitrary criteria using Python and AI. I asked about search terms for **YouTube/Google**, Python company prospecting, lead-generation scripts, and whether using an **agent framework** from the beginning might have helped. I inspected or discussed a lead-generation tutorial and the `bottomlineops/PythonGenLeads` GitHub project. We acknowledged that an agent framework was not automatically the missing ingredient: search coverage, source reliability, factual verification and criteria screening remained hard problems. I chose not to rebuild the search layer under deadline; improvements could be revisited later without having to rebuild the dashboard.
- I asked what **"roughly $1M–$10M"** meant, whether companies above $10M count, and how to treat estimates. We kept the requested band as the target; above it should not silently be counted as an exact match. Sourced estimates are lower-confidence evidence, not independently verified facts.
- I considered **Google Calendar** first and then Cal.com. We discovered the often-mentioned seven-day Google OAuth issue was specifically relevant to external apps in Testing mode, not a blanket expiration of every Google Calendar integration. Nevertheless we kept **Cal.com**, because its scheduling/availability/booking API better matched our need. Cal.com was later connected to Google Calendar and used **Cal Video** for the meeting location.
- I reduced a proposed **10-table** database design to a more explainable **six core operational tables**: `companies`, `reasons`, `threads`, `messages`, `suppressions`, `bookings`. Supporting access-control and other implementation tables/functions were added as needed (e.g. `reviewers`); six refers to the original core model, not proof that the final live schema contains exactly six tables.
- I deliberately constrained `bookings.status` to `booked` and `cancelled`; absent booking means there is not yet a confirmed meeting. I questioned which company states should transition after cancellation and kept `opted_out` as higher priority than `replied`.

## 2. Research pilot: methods, failures, and the decision to stop over-optimizing

**Pilot architecture and limits**

- Used **Tavily** search to find candidate companies and source URLs, **HTTPX + BeautifulSoup** to retrieve website content, **Gemini** to interpret claims and extract structured JSON, and evidence/scoring checks in Python. A **Tavily Basic Extract** fallback was added when websites blocked direct fetches with HTTP 403.
- Research JSON retained *all* attempted companies including failed/uncertain/poor-fit candidates, with evidence URLs, quotes, missing data, errors, confidence and independent scoring breakdown. A source quote appearing verbatim does **not** automatically prove the conclusion attributed to it.
- Pilot run limits included 15 Tavily searches, 30 URLs, 10 Gemini requests, and one Tavily Basic Extract fallback per company (as documented at that stage). These were budget/rate controls for the pilot, not universal final platform limits.
- Scoring: **25 US base + 25 own-brand e-commerce + 15 revenue + 15 employees + 20 timing = 100**. A sourced estimate for revenue/headcount could receive partial credit. Outreach-timing points were distinct from the 80-point profile fit.
- **Concrete corrections:** Gemini `AttributeError` due to malformed model output; Gemini HTTP 503 retry/backoff; websites returning 403; successful Tavily extract test on **Lulus (5,884 characters)**; avoiding treating press-release datelines as HQ proof; quarterly revenue not equal to annual revenue; disqualifying "over 120 employees" for 5–50 headcount; not treating **Richpanel** (software vendor to e-commerce businesses) as a consumer-product e-commerce brand; correcting **Pique** fit/scoring discrepancy when a storefront quote matched but `unclear` suppressed points; screening likely candidates before selecting the first three.
- A reviewed three-company pilot with **Grüns, Cotto, All In Food** produced **zero confirmed full fits**. Later connected research batches (reported in the project discussion) also included **Crane Baby, Empava Appliances and Miracle Brand**; one reported batch saved three companies but zero confirmed matches, with around 50-point scores. A fallback from Gemini to **Groq** was reportedly added in research, along with a bounded attempt cap. These implementation details are reported outcomes, not proof all providers worked on every run.
- I asked whether to replace Gemini, compare against another LLM, use agent frameworks, or keep tuning search. I eventually chose to **stop optimizing the pilot** and build the complete product first. I did not want the research problem to prevent finishing Gmail, human review, booking, and dashboard.
- Later, multiple saved research/company records existed (including demonstration-fit records). At one point I discussed **about 30 company records** and four demonstration fits, but this was **not the same as the required 30 confirmed ICP matches**. IQBAR was the clearest observed fit; Torque Fitness and Simple Sugars were used for controlled product workflow testing. Do not report the 30-company target as met.

## 3. Tomba, contact discovery, and lead handling

- Contact sourcing was a **separate stage** from company profile qualification. I wanted senior/appropriate contacts rather than assuming any inbox owner was an ideal lead.
- Added or tested **Tomba** domain-based contact lookup as part of contact discovery. The official Python quick-start pattern that I asked to preserve for Codex was:

```python
from tomba.client import Client
from tomba.services.domain import Domain
import os

client = Client()
client.set_key(os.environ["TOMBA_API_KEY"])
client.set_secret(os.environ["TOMBA_API_SECRET"])
result = Domain(client).domain_search("stripe.com")
print(result)
```

  The exact installed SDK API should be checked against the package version; this is a preserved reference pattern, not evidence that `stripe.com` is a Ledgerwise prospect. Never put real tokens in code.
- **Simple Sugars example (company 33):** contact review surfaced general `info@`, `wholesale@`, `press@`, ambiguous `lani@` and `caroline@`, and **Gina Lazzari (VP), `[email redacted]`**. All were **delivery unverified**, roughly 50–55% displayed confidence. I chose Gina for the controlled test because she was a named senior contact; no claim of live email deliverability was made.
- **Torque Fitness example:** saved contact **Brandon Brambilla** (director-level Key Accounts Midwest) and address `[email redacted]` (unverified); contact ID **28**. Emails were still sent to `TEST_EMAIL`, not to him.
- Preserve distinctions among **contact found**, **role plausibly relevant**, **delivery verified**, and **actual business ICP fit**; these are not interchangeable.

## 4. Building the working platform and database

- Final stack: **FastAPI** Python backend, server-rendered **HTML/CSS/JavaScript** dashboard, **Supabase PostgreSQL** and **Supabase Auth**, plus external **Gemini, Tavily, Tomba, Gmail OAuth, Cal.com** integrations. Research also tested an alternate LLM fallback. No agent framework was required to implement the final email/booking workflow.
- Six sidebar sections stayed: **Overview, Companies, Email Drafts, Conversations, Meetings, Settings**. Overview functions as a queue/metrics view, Companies shows research/fit/evidence/contacts, Email Drafts has versioned review, Conversations shows full threaded history and ownership, Meetings shows booking data and calendar/detail UI, Settings includes account and role-based employee access.
- **Checkpoint order changed over time.** I initially wanted to test research first, then login, then deployment. Later I agreed on authentication, employees/security, connected research, emails, replies, booking, deployment. As the deadline approached I prioritized the complete platform over perfect research and asked that Codex make no unapproved structural changes.
- I rejected manually populating test companies with `seed_company.sql` or direct hand insertion; the research button should create genuine research records. A pilot was eventually connected to Supabase, and the platform was demonstrated with researched and demo-fit companies.
- Early Supabase schema lacked the backend's required grants after I had deselected a grant option; I noticed and Codex investigated. I also asked about using a database connection string. We kept Supabase's HTTPS API with `SUPABASE_URL`, publishable key, backend secret/service-role key, rather than requiring a separate `DATABASE_URL` connection for the current implementation.
- I explicitly learned the difference between Supabase's **project admin account**, a **Supabase Auth user**, and an application **reviewer/admin** in `public.reviewers`. A single env-allowlisted reviewer ID was too restrictive; we moved to a table with role/active access.
- Login's first real error **"Submit the form from Ledgerwise."** was traced to origin/referrer configuration and fixed by using `Referrer-Policy: same-origin` instead of `no-referrer` (as reported by Codex). Login/logout/access-denial/role revocation tests subsequently passed with real Supabase accounts.
- Employee management decisions included invite-based enrollment, no public signup, first admin seeded via Auth/reviewers, admin-only role management, last-admin protection, password hash storage by Supabase Auth, password reset via admin-verified link, and a read-only-then-Edit interface. Exact completion and live testing of every employee-management feature is **not independently established** by the current notes; preserve it as a design or reported implementation rather than asserting full production readiness.
- Security concerns I raised: `service_role` bypasses RLS, so FastAPI must check session, active reviewer and role before privileged queries; backend-only secrets; CSRF/origin checks; RLS and browser-role grants; correct URL redirects. I repeatedly forbade secrets in GitHub, and later planned a read-only SQL audit before publishing code.

## 5. Email safety architecture and Gmail connection

- **Every outbound email**—first draft, AI reply, human-written reply, scheduling offer, and Ledgerwise meeting confirmation—requires **human approval of the exact saved version**. Editing must invalidate prior approval; obsolete rejected or superseded versions stay in history but must not be sendable. No AI-only automatic sending.
- **Test Mode**: all actual recipients are my configured `TEST_EMAIL` inbox. Saved company business email remains attached for research/contact context but is **not** the Gmail delivery address. This was an intentional safety requirement of the Unscale case study.
- Gmail OAuth was configured and tested; Gmail uses its own generated `gmail_thread_id`, and Ledgerwise stores that value for syncing replies back into an internal `threads.id`. I learned that these two IDs identify different things. Replies had to remain in the correct thread, with correct reply headers and Gmail quoted history stripped before intent classification.
- Distinction between **real prospect address** and **test inbox** became critical later for opt-out, because suppressing the global `TEST_EMAIL` would accidentally block testing for every company.
- At an intermediate checkpoint, Codex reported implementation of versioned email editing/rejection/approval and Gmail test-only sending with **24 mocked tests passed** and a SQL migration; OAuth configuration was still ongoing then. Later the user successfully approved and received many real controlled emails.
- We kept the actual ledger of outgoing/incoming messages, versions, send status, AI decisions, provenance, reviewer identity and approval audit history in Supabase. Unknown/ambiguous Gmail delivery status must not lead to a blind resend.
- I asked Codex to explain migrations, sensitive permissions and every changed file before edits, particularly when database functions (`review_initial_message`, `review_reply_message`, ownership checks) authorize sending.

## 6. AI replies: rules I insisted on

- Ledgerwise is only confirmed to offer **monthly bookkeeping for e-commerce brands**. The assignment did **not** establish specific reconciliations, tax filing, payroll, accounting-software integrations, audit-readiness, reports, guaranteed outcomes, custom packages, fixed packages, or an exact pricing amount.
- **General bookkeeping explanation** may say, when clearly framed generally, that monthly bookkeeping involves organizing and maintaining financial transaction records over time. It must not turn that general definition into an unsupported promise of Ledgerwise's specific deliverables.
- **Pricing rule:** Pricing details belong on a call; never invent dollar amounts, fixed packages, customized pricing, discounts, or fee-calculation policy. After many failed dynamic pricing attempts, I **implemented an approved fixed template for standalone pricing questions** (see timeline below). Mixed-intent and explicit scheduling requests still use their respective paths.
- **AI identity:** If asked whether the correspondent is human or AI, clearly disclose AI assistance and that a human reviews replies before they are sent; don't impersonate a person, don't escalate unnecessarily.
- **Objections:** Acknowledge concerns such as existing in-house bookkeeping; explain the approved monthly service without promising more oversight, integrations, outcomes or guarantees; do not pressure the prospect into a meeting.
- **Unconfirmed additional services:** Tax filing, payroll, monthly financial reports or QuickBooks integrations should trigger an honest limitation/Need Human rather than fabricated assurances. Human reviewer can draft a truthful response saying availability must be confirmed.
- **Opt-out:** A clear request to stop emails should result in durable contact-level suppression, not merely a warning. This was **NOT fully implemented by the project's final checkpoint**; see the explicit limitation below.
- **Booking:** Merely mentioning a call in an email is not the same as the prospect actually requesting scheduling. A real scheduling request gets Cal.com availability; a pricing question does not. Actual booking requires an offered slot, explicit selection, live availability recheck, duplicate protection and provider confirmation.
- **Human Takeover:** Terminal invalid AI replies, unknown services or policy issues should be flagged; no unverified draft is sent. Human replies still pass required send safeguards and approval. Returning to AI may require the human to have actually sent an answer to the newest inbound message.

## 7. Exact late-night development and live testing (20–21 November)

### 7.1 Initial IQBAR booking — first proof Cal.com worked

- **IQBAR**, from the researched/test companies, went through a real controlled email exchange. The prospect asked to arrange a meeting **next week**, and Ledgerwise eventually offered actual Cal.com slots in **America/New_York (EST)**.
- Parsing bug: an email used line breaks, so matching `call next week` as a contiguous substring failed when Gmail normalized it as `call\nnext week`. Codex changed whitespace normalization; the explicit request started matching.
- Confirmation bug: **"Option 1 works for me. Please book it."** was not initially parsed reliably because of trailing confirmation language. Codex corrected option detection.
- A real test booked **Monday, 23 November 2026, 9:00 AM EST**, 30 minutes, via Cal.com event type ID **8009832**; Google Calendar was connected and the meeting used Cal Video. It appeared in Ledgerwise Meetings and in Cal.com, and Cal.com sent its own provider confirmation notification. A separate Ledgerwise confirmation email remained subject to human approval. `CALCOM_BOOKING_ENABLED=true` and `GMAIL_SEND_ENABLED=true` were enabled for controlled live testing; check their current state before deployment or public demonstration.
- Important nuance I asked about: Cal.com's own automated booking email can be sent even while Ledgerwise's separate email draft is pending approval. The human-approval guarantee governs emails Ledgerwise sends through its Gmail workflow, **not** Cal.com's provider-generated notices.
- Earlier test cases included no booking until an explicit slot selection, timezone display, offered slot IDs, rechecking availability, duplicate bookings after retry/timeouts, and human scheduling options. **Booking itself was truly live-verified once for IQBAR**, not just mocked.

### 7.2 Torque Fitness — first email, service, pricing, identity, objections

- The Torque Fitness contact was **Brandon Brambilla**; the outreach used real company research but delivery to `TEST_EMAIL`. Initial AI email reached a successful approved **Version 2**, mentioning observed products such as rigs and racks. Emails remained in a Gmail thread with in-app history and approval.
- **Service request:** Brandon asked, "Could you explain what Ledgerwise actually offers and how your monthly bookkeeping service works?" Several real Gemini replies invented transaction categorization, financial statements, bank reconciliation, audit readiness or integrations. Gemini's evidence verifier rejected unsupported capabilities. Codex introduced stricter reply grounding, correction feedback, general bookkeeping definition routing, and clearer diagnostics. **Version 3** eventually passed and was approved:

  > Hi Brandon,  
  > Ledgerwise provides monthly bookkeeping services specifically tailored for e-commerce brands. We assist businesses by maintaining their monthly financial records on an ongoing basis.  
  > Would you be open to a brief call to discuss how our service could align with the needs of Torque Fitness?  
  > Best, Ledgerwise Team

  This was accepted in the real flow at the time, but later we discussed that "tailored" and direct promises about the firm's processes are not necessarily grounded. A verified reply is not a guarantee every claim is beyond criticism.
- **Pricing question:** "How much does Ledgerwise charge per month? Do you have any fixed pricing packages?" Gemini correctly recognized pricing but initially claimed **"we do not have fixed packages"** and **"fees are tailored"**—both unsupported. ChatGPT suggested manually editing rather than weakening validation. Several later changes to standalone pricing instructions, shared revision, verifier, and routing followed.
- **AI disclosure:** "Before we continue, am I speaking with a real person or an AI assistant?" The real response correctly disclosed AI assistance, human review and did not unnecessarily take over. **Live passed.**
- **In-house objection:** "We already have someone handling our books internally. Why should we consider Ledgerwise?" First draft invented seamless integration with existing workflows, correctly rejected. Another retry failed the generic combined copy guard (which could match innocuous phrases). Codex reported the precise validator path and improved rejection detail and objection-specific instructions. **83 mocked tests passed**, and the next real objection response was accepted. This success is specific to the saved reply; not a universal guarantee.
- **Unsupported services:** Prospect asked about tax filing and payroll. AI escalated to **Needs Human** and did not send an invented claim. We used the existing manual reply workflow, e.g. confirm monthly bookkeeping only and say other services must be checked before answering.
- **Return to AI Assistance:** A later reply about service triggered verification failure again (claimed special design/record maintenance); a newly rejected human-written draft did **not** itself block return. Codex inspected the saved state: latest incoming message **387** had no **sent** reply, and ownership rules required a human-sent response to the most recent inbound after a flagged handoff. Therefore "Cannot change ownership. Check opt-out, handoff and latest reply status" was an expected safeguard, not proof the database was corrupt.
- **Reset idea:** I considered deleting Torque and starting all over. Codex investigated thread **2**: **9 sent messages, 10 received, 4 rejected drafts, 9 AI decisions**, no bookings or unknown-progress sends; first-email job still pointed to contact 28. It proposed archive + reset + new Gmail thread requiring a migration/active-thread checks. **I did not approve or implement this** because it was riskier and unnecessary for the demo. We chose another fresh company instead.

### 7.3 Why many incremental Gemini fixes kept failing

- We repeatedly passed mocked tests but encountered real Gemini failures for simple service and pricing questions. Issues included:
  - Different question phrasings selected different knowledge branches; "what does it involve/how does it work" sometimes missed the general bookkeeping definition.
  - Generator and verifier had access to the same data **within a selected branch**, but related questions got inconsistent branch contexts.
  - The service evidence verifier treated a faithful broad explanation as if Ledgerwise guaranteed a particular deliverable, while the first-email verifier sometimes allowed paraphrases more generously.
  - Gemini appended unsupported qualifiers: **"specifically designed"**, **"integrates seamlessly"**, **"helps support growth"**, **"customized fees"**.
  - Pricing verifier gave **contradictory verdicts**: it first demanded an acknowledgment about packages/variable fees, then rejected the corrected acknowledgment as unnecessary.
  - Some corrections failed generic email formatting (greeting, blank lines, two-line signature) before reaching evidence verification.
  - An ordinary pricing question sometimes received Gemini action `suggest_call`/`scheduling_interest`, bypassing the standalone pricing check. The reply's own invitation to discuss pricing was confused with the inbound prospect's actual request for scheduling.
- Codex added a shared **ordinary reply grounding** block splitting (1) confirmed Ledgerwise facts and policies, (2) general bookkeeping knowledge, (3) unconfirmed commitments; both `generate_reply()` and `verified_reply_copy()` used it. The general correction path allowed **one evidence-based revision** for ordinary answers/service/objections without changing the selected route, followed by all checks and Needs Human on terminal failure. This improved some real service answers but did not make pricing reliable.
- A real **draft-only** replay of Simple Sugars service message **422** eventually passed Gemini generation + evidence verification on the first attempt without altering saved messages, using two Gemini calls (write and verify). Later the same message passed after one correction under additional changes. These are distinct replay results, not a contradiction: different code states and samples.
- I eventually said that I would rather go back to the **last observed working pricing behavior** than keep making patches. Codex could not restore an exact old version because `ai_reply.py` was **untracked and had no Git commit history**. Torque's sent message **303** proved a successful **`pricing_inquiry` + `handle_objection` ordinary action** and a short policy-only call invitation, but not its original prompt source.
- Codex found the routing gap: standalone pricing applied only if the model also selected an ordinary reply action. It changed `generate_reply()` so standalone pricing cannot become a Cal.com offer merely because Gemini suggests a call. Live replays then confirmed action routing but **still failed formatting and inconsistent evidence verification**. Mock counts reached **97** in one branch, but live failures remained.
- We tried carefully scoped policy-only verification and allowed-invitation variants. Real Gemini still produced new harmless phrasings or mixed replies that ended up in contradictory verification. **Decision: stop asking Gemini to generate/verify a business rule whose answer never changes.**
- **Final implemented pricing solution:** For a **standalone pricing / fees / costs / package** question, `generate_reply()` uses an **approved deterministic pricing email template** with proper name fallback and a simple statement that pricing is discussed on an introductory call. It makes **zero Gemini and zero Cal.com calls** for that specific category. Mixed-intent or explicit meeting requests keep existing routing, and every outgoing version still requires human approval. Codex reported **96 mocked tests passed** after this solution. A real Simple Sugars pricing exchange later successfully produced, approved, and sent the appropriate template reply. This was the decisive practical resolution.

### 7.4 Simple Sugars — clean end-to-end test

- I chose **Simple Sugars** (company **33**) for a new workflow, contact **Gina Lazzari**, a VP with unverified business email. Codex reused the saved contact after failed draft attempts, avoiding a new Tomba lookup.
- **First email failed multiple times** in `format_first_email()` for superficial closing questions. An early failure said: "First outreach can invite a conversation without proposing times or booking." Another said: "First outreach must end with a question asking if the prospect is open to learning more or talking." Codex proved that "Would you be open to hearing more?" was incorrectly rejected, while "Would you be open to a brief conversation?" passed; "Would a call Tuesday at 10 AM work?" was correctly blocked. Adding a few accepted phrases was not sufficient because the next Gemini draft used another valid wording.
- **Resolution:** Remove the brittle invitation keyword/phrase whitelist **only in the first-email validator**; require exactly one closing question, prohibit concrete times/booking links/direct booking requests, and let the existing Gemini first-email verifier judge whether it invites discussion and whether claims are grounded. Do not touch the separate reply pipeline. Codex reported **91 mocked tests passed**. The real Simple Sugars first email then generated and was sent successfully to `TEST_EMAIL` after my approval.
- That initial email referred to Simple Sugars' products and subscription options. ChatGPT suggested a safer edited version because of phrases such as "support their growth" and "specialized bookkeeping"; **I explicitly chose to send the generated version as-is**. This is an example of a decision I made after considering but rejecting an edit.
- **Real service reply test:** Gina asked what Ledgerwise monthly bookkeeping involves/how it works. A later real Gemini answer correctly separated a general bookkeeping explanation from confirmed company facts; the draft passed, I approved/sent it. Example wording:

  > Hi Gina,  
  > Monthly bookkeeping generally involves the ongoing process of organizing and maintaining financial transaction records. Ledgerwise is a US bookkeeping firm that provides monthly bookkeeping services specifically for e-commerce brands.  
  > We would be happy to discuss how our services could align with your needs during a brief call. Would you be open to that?  
  > Best, Ledgerwise Team

- **Real pricing test:** Earlier attempts failed despite extensive changes. After approved-template fallback, a saved Simple Sugars standalone pricing request produced and sent a safe approved response. Another later pricing regression also displayed the correct draft and no extra booking.
- **Real objection:** Gina said she already had someone doing bookkeeping internally. Gemini generated an objection reply that passed the system's checks, but ChatGPT spotted a claim about "additional oversight"/specialization that was not confirmed. It recommended a manual edit to a more defensible answer; outcome of that specific edit/send is reported in the discussion as an intended action, not proven from every screenshot.
- **Real disclosure:** When asked whether the sender was AI, the system correctly disclosed AI assistance. **Passed.**
- **Unsupported extras:** Gina asked whether Ledgerwise provides **monthly financial reports** and **QuickBooks integrations**. The system flagged **Needs Human** with no unsupported AI reply. I drafted a human response confirming only the broad monthly bookkeeping offering and acknowledging those specifics would need verification. The human-sent reply and Return to AI were used before continuing the booking flow.
- **Gina's explicit meeting request** (saved incoming **891**): "I'd like to schedule a call with Ledgerwise next week. Could you share a few available time slots?" Initially the classifier returned `clarify`, and the generated email wrongly asked for preferred date/time/timezone instead of offering availability. Codex confirmed `scheduling_request()` in `calcom_booking.py` recognized the shorter IQBAR phrase but missed **"with Ledgerwise"** and a follow-on sentence after "next week". Gmail line-break normalization was already correct. **Isolated change to that matcher** made Gina's request return `next_week` and reuse IQBAR's availability path. No booking creation code changed. **96 mocked tests passed**.
- **Actual Cal.com offer** from outgoing draft **Version 3** (after regenerate from saved Version 2):
  - **Option 1:** Mon **23 November 2026**, **9:30 AM EST**
  - **Option 2:** Mon **23 November 2026**, **10:00 AM EST**
  - **Option 3:** Mon **23 November 2026**, **10:30 AM EST**
  - The offer required approval and said "Please reply with the option number you confirm." I approved and sent to `TEST_EMAIL`.
- Gina's controlled Gmail reply: **"Option 2 works for me. Please book it."** The app recognized **"Slot confirmed · Mon, Nov 23, 2026 at 10:00 AM EST"**, then the Meetings dashboard displayed **Simple Sugars** as **Upcoming** at **10:00 AM EST** alongside **IQBAR 9:00 AM EST**. This is the second **real Cal.com booking flow** observed in the project. The `Upcoming` dashboard status is not by itself proof of every provider-side constraint; the prior live provider confirmation and tests support booking correctness.
- **Booking concurrency read-only mock study:** Codex verified (1) a slot disappearing between offer and acceptance → availability rechecked and alternatives drafted, no booking; (2) slot booked before retrieval → not offered if Cal.com excludes it; (3) duplicate confirmations in same conversation → one mock booking and confirmation, while SQL guard was read but **not executed against live PostgreSQL**; (4) two different prospects confirming same time → both rechecks could pass, mocked Cal.com accepted one and returned HTTP **409 Conflict** for the second, whose conversation went to human without a success confirmation. There is **no cross-conversation slot reservation guard** in Ledgerwise; the live Cal.com provider must reject conflicting simultaneous bookings. **No change to booking code** was approved after this assessment.
- **Post-booking regression:** I re-asked about the bookkeeping service and then about pricing before the scheduled call. Both produced acceptable pending drafts; service explanation did not create another booking, and pricing used the approved policy. This confirmed recent scheduling matcher changes had not obviously broken those reply types in actual controlled testing.

### 7.5 Opt-out test — confirmed missing safeguard; deliberately postponed

- Last planned test was an opt-out because suppression could prevent later test sends: Gina wrote, **"Please remove us from your contact list and don't send us any more emails."**
- The system detected an opt-out-like message and showed **"Possible opt-out needs confirmation"** / **Needs Human**, with **no AI sales reply**. Initially that looked acceptable, but I asked whether it had actually suppressed Gina.
- Read-only Codex inspection of saved message **1152** confirmed **no suppression record** for the original business address or `TEST_EMAIL`. Simple Sugars still showed **Booked**. The handoff alone is not durable send prevention; a human reviewer might regain the ability to send. The existing clear-opt-out import branch could attempt to suppress **both** the real business contact and the shared `TEST_EMAIL`—unsafe because it would interrupt all other test companies. **This is an unresolved safety gap, not a passed opt-out enforcement test.**
- I proposed a safer, simple **human-controlled** workflow: opt-out detected → `Needs Human` → `Take over` → **Mark as Opted Out** button next to takeover controls, without needing to send a manual reply. While pending, all outbound email should be blocked. On confirmation, atomically suppress only the original business contact, mark the company opted out, retire unsent drafts, keep history and already-booked meetings, and leave `TEST_EMAIL` operational.
- Codex researched the proposal: `templates/conversations.html` button/badges; `main.py` POST action plus pre-Gmail send recheck; targeted SQL changes to `review_initial_message()` and `review_reply_message()` for a pending send hold; ownership/return guard; new transactional `mark_conversation_opted_out()`; change what `import_gmail_reply()` does after detecting clear opt-outs (not the detector itself). It said an **SQL migration would be required**, although existing tables/columns can store state. Tests should cover stale browser tabs, previously approved drafts, manual replies, audit, `TEST_EMAIL`, and a confirmed booking left unchanged.
- I became worried that a new migration and send-authorizing SQL changes at **~07:00 without sleep** would break tested functionality or delay GitHub, deployment and video. **Final decision: SKIP / POSTPONE the opt-out button/migration.** Do not claim permanent suppression works. For the take-home, keep outbound sends limited to controlled test inboxes and document **"opt-out detection + human escalation exist; durable original-contact suppression and global send blocking are required before production"**. This decision was made consciously to protect the deadline and working demo, not because the safety bug was resolved.

## 8. Other important workflows and questions I asked

### 8.1 Approval mechanics and send status

- I asked whether a human should approve **only first emails** or **every email**; chose **every outgoing version**, including AI-created and manual copies.
- I asked how to safely distinguish **Approve**, **Send Approved**, **Reject**, **Edit**, **Regenerate**, and **Retry AI reply**. We ensured edits invalidate approval and previous versions stay in history; an invalid regeneration can retire an older draft so it cannot still be approved accidentally.
- I asked what to do if I receive **"Generating AI reply. Select the company again to check progress"**—this indicates processing, not necessarily a completed booking. We checked Meetings and provider confirmation rather than treating a status label as final proof.
- I wanted the supervisor to see **why** AI handed off, and to have a clear **Take over → manual draft → Approve and send → Return to AI** path. We discovered that a rejected human draft does not count as a sent response for ownership release.
- I asked to keep failed and rejected copies auditable but no longer sendable, and insisted that Test Mode never silently switches to the real company contact.

### 8.2 Scheduling and conflicts

- I asked why Ledgerwise asked for a preferred date/time instead of retrieving availability when the prospect requested next-week options. The fix was to recognize genuine **intent expressed by the inbound message** and not demand an exact day when the prospect explicitly wants the system to offer slots.
- I asked whether available slots were truly fresh and whether someone could book a time **before offering** or **between offer and confirmation**. We checked Cal.com availability before offering and rechecked immediately before booking. A mock conflict study showed the remaining two-prospect race relies on provider-side HTTP 409.
- I insisted no booking just because the email says "let's talk" or a pricing reply invites a call. Only an actual customer meeting request can initiate availability, and only an explicit accepted slot can create the booking.
- I asked whether human scheduling should be possible; the human reviewer can select up to **three verified slots** through the existing workflow, with the same backend booking safeguards and approvals.
- I wanted the Meetings page to show dates, statuses, and details. The live UI displayed IQBAR and Simple Sugars bookings as Upcoming, with date/time and a detail/calendar view.

### 8.3 Product UX and ownership decisions

- Wanted a polished but nontechnical employee-facing dashboard, not just backend scripts; research results visible, outreach statuses, filters and work queue, review/edit/reject modals, conversation history, handoff badges and meeting data.
- I repeatedly questioned why a proposed field, file, table or admin permission existed. I tried to understand Codex's edits before saying yes, so I could explain the implementation in the assignment and avoid unnecessary complexity.
- Asked about the difference between company status, conversation ownership, Gmail delivery target and original business address, and what a rejected draft means for a thread.
- Asked whether to reset the entire Torque conversation after multiple tests; **declined** the archive/reset proposal because it needed migrations, and started a cleaner Simple Sugars test instead.
- Asked whether earlier approved features could break because of a tiny fix. We instituted broad mock regressions but eventually learned they are **necessary, not sufficient**; draft-only real Gemini replay is a stronger targeted check for LLM generation and verification.

## 9. Changes to AI reply code and why each was made

> This is a **conceptual modification log** based on Codex reports, not a byte-for-byte diff. Exact Git commit history is unavailable for some files (notably `ai_reply.py` was reported untracked at one checkpoint).

| Component / mechanism | Reason introduced or changed | Final interpretation |
|---|---|---|
| `generate_reply()` routing | Gemini mistook a pricing reply that invited a call for a scheduling action | Standalone pricing stays ordinary; explicit meeting requests keep scheduling route |
| Ordinary-reply grounding | Different phrasings got different company/general knowledge; verifiers disagreed on service descriptions | Shared context splits confirmed facts, general definition and unsupported commitments |
| Shared evidence revision | Repeated unsupported capabilities caused terminal failure immediately | One targeted revision on ordinary reply actions, then all checks, then Needs Human if needed |
| Service/objection guidance | Replies invented reconciliations, reporting, seamless integration, specially designed support | Grounded short answers; explicit unsupported claims still fail |
| Pricing prompting/verifier experiments | Conflicting judgments about packages/variable fees and formatting | Superseded by the deterministic standalone pricing template; avoid adding more Gemini pricing exceptions |
| `verified_reply_copy()` diagnostics | Generic "pricing, scheduling or invalid copy" hid specific reject cause | More precise rejection info on affected paths; still enforce formatting/safety |
| `format_first_email()` | Natural invitations rejected by keyword lists | Require final question and prohibit actual scheduling; let evidence/invitation verifier judge meaning |
| `scheduling_request()` | "with Ledgerwise next week" plus availability follow-up missed match | Reuse existing next-week Cal.com availability route; no booking logic changed |
| Needs Human fallback | Invalid AI generation left repeated errors | Terminal generation/verifier failures flag Need Human and retire stale sendable drafts |
| Opt-out enforcement | Real explicit opt-out only flagged, not permanently suppressed | **Known incomplete feature, implementation postponed** |

## 10. Verification timeline and what "passed" means

- Codex reported suites increasing over the project: **24** mocked email tests at an intermediate point, then around **83, 85, 87, 88, 90, 91, 92, 93, 94, 95, 96, 97**, and later **96** tests after the final pricing and scheduling changes. Counts changed because test suites and branches changed; they are **not a single cumulative proof of 97 distinct production behaviors**.
- Repeated observation: **all mocks passed while a live Gemini reply still failed**. I changed the success criterion from "mock tests passed" to "real Gemini draft-only replay with exact saved question, recorded route, format result, verifier result, correction outcome, no Gmail/Cal.com side effects".
- **Live verified:** controlled Gmail first outreach (Torque, Simple Sugars), multiple AI response types (service explanations, pricing template, identity, objection with human review), explicit unsupported-service handoffs, human approval/sending, IAM login checks earlier, **two real bookings** (IQBAR 09:00 and Simple Sugars 10:00 EST on Mon 23 Nov 2026).
- **Mock or read-only only:** cross-conversation booking race; SQL concurrency lock under live PostgreSQL; all edge cases of approval/suppression; the final opt-out button/migration (not implemented); full production-grade authorization against every threat; and 30 confirmed ICP companies (not achieved).
- **Opt-out:** explicit opt-out detection and `Needs Human` is **live observed**, but **suppression enforcement failed the test** and is not complete.
- **Deployment:** Not yet confirmed deployed or published from the information available at this checkpoint. Do not mark it complete.

## 11. Project status as of 21 November 2026

| Workstream | Status | Honest evidence / limitation |
|---|---|---|
| Product planning and platform architecture | **Implemented** | FastAPI/Supabase/HTML-CSS-JS, six-page dashboard |
| Supabase Auth reviewer role checks | **Live tested** | Login/logout/access denial/revocation were tested; audit precise employee invitation scope before claiming more |
| Research pipeline | **Implemented but incomplete** | Tavily + fetching/extract + Gemini/alternate fallback + evidence/score; research saved candidates, but 30 confirmed ICP fits not achieved |
| Tomba contact lookup | **Used** | Saved contacts; some delivery addresses unverified |
| First outreach | **Live tested** | Generated, verified, versioned, approved, sent to test inbox; one first-email validator false-positive class was fixed |
| Gmail threading/replies | **Live tested** | TEST_EMAIL, reply history, Gmail thread mapping, quoted-history handling |
| Ordinary AI replies | **Live tested with limitations** | Service, disclosure, objection, human handoff, human review; model/verifier can still make mistakes |
| Standalone pricing | **Live tested** | Final deterministic approved pricing template, human approval; no Gemini/Cal.com on pricing-only path |
| Cal.com availability and booking | **Live tested twice** | IQBAR and Simple Sugars bookings on 23 Nov; concurrency cases partly mocked, provider-side race relied on Cal.com |
| Human takeover + return to AI | **Live tested** | Unknown services and terminal failures; latest inbound may require a sent human reply before returning |
| Opt-out detection | **Live tested** | Explicit request recognized/flagged, but not durably suppressed |
| Opt-out permanent suppression | **Not completed — intentionally postponed** | Human-confirmation button/DB send hold proposed only; unsafe to use with real prospects |
| GitHub public repo and SQL reproducibility | **Outstanding / audit planned** | Need final schema/functions/migration inventory and secret scan; do not assume historical SQL saved |
| Deployment | **Outstanding / not verified** | May use Render and existing Supabase DB; require runtime config and OAuth redirect review |
| Demo video / submission | **Outstanding / not verified** | Loom demo ≤10 minutes, README, HOW_I_WORKED.md, unedited AI coding sessions and final checks |

### Final constraints and priorities I chose

1. **Freeze tested working functionality** unless there is an actual critical defect; do not touch Cal.com booking merely because an unrelated Gemini email fails.
2. **Keep TEST_EMAIL-only mode**. Actual prospect contact emails may be stored, but never send unsolicited production outreach through this prototype while suppression is incomplete.
3. **Do not misrepresent limitations:** 30 confirmed fits not met; opt-out suppression not complete; mocked concurrency is not live database concurrency; a generated email accepted by Gemini's verifier is still subject to human judgment.
4. **Finish submission deliverables first:** safely prepare and push the code, check final SQL state, README/environment template, deploy if possible, record demo, provide notes on how I worked with AI, export Codex sessions, submit.
5. **Don't deploy secrets:** `.env`, Tomba/Tavily/Gemini/Groq keys, `SUPABASE_SECRET_KEY`, Gmail client secret/refresh tokens, Cal.com API key and OAuth credential files stay out of GitHub. Use placeholders in `.env.example` and deployment environment secrets.
6. **Keep the explanation honest:** This is a supervised, test-mode AI SDR prototype with researched leads, grounded AI drafting, exact-version human approval, Gmail threading, human escalation and real Cal.com scheduling; not a fully autonomous production sales system.
7. **Sleep and submit a credible partial result rather than risk destructive changes** in the final hours. I deliberately postponed an opt-out SQL migration near 07:00 after an all-night session because GitHub/deployment/video were still outstanding and I had not slept.

## 12. How I used ChatGPT, Claude and Codex — first-person record for HOW_I_WORKED.md

> I did not just paste the assignment into a coding agent and accept whatever it produced. I started by translating the assignment into clear requirements, questioning the feasibility of finding 30 qualifying companies, designing a scoring approach, and deciding how much automation was appropriate. I used **ChatGPT** to understand technical concepts and evaluate options, structure my own ideas, review bugs and screenshots, and turn unstructured questions into precise prompts for Codex. I also consulted **Claude** for planning and Git/project workflow; I would verify the original Claude conversation before attributing any more specific suggestion to it. **Codex** was my development agent in VS Code: it inspected the repository and Supabase data, wrote and modified Python/SQL/frontend code, proposed fixes, ran automated regressions and, when authorized, performed controlled live or draft-only tests.
>
> I personally decided to prioritize manual human approval of every outgoing email, Test Mode with a single inbox, strict evidence tracking, a simpler database, admin/reviewer separation, the six-page dashboard, Cal.com for actual scheduling, and explicit human takeover when the AI cannot safely answer. I rejected premature login/deployment work before validating research, manual company seeding, broad code rewrites, automatic real-prospect sending, risky conversation resets, and an unfinished opt-out migration at the end of the deadline.
>
> My iterative process was: articulate the intended behavior → ask ChatGPT/Claude for alternatives and explanations → give Codex a bounded instruction → inspect Codex's implementation plan → approve or reject changes → test in the real application → capture failures → demand the actual root cause and reproduce the failure → make a small correction with regression checks. I learned that 90+ passing mocked tests do not guarantee stable live LLM behavior. We sometimes made too many narrow patches, especially around pricing. I eventually chose a deterministic, human-approved pricing template instead of continuing to make Gemini and its verifier agree on an unchanged business policy.
>
> I verified controlled Gmail sending and two real Cal.com bookings, tested unsupported requests and Human Takeover, and deliberately documented what was still unfinished. The result is a working supervised SDR prototype, with known limitations in company qualification completeness and permanent opt-out enforcement. I used AI assistants for implementation and reasoning, but the specifications, trade-offs, testing feedback and final decisions were actively directed by me.

## 13. Precise questions / decision register (fast lookup)

| Question or idea I raised | What we concluded | Outcome |
|---|---|---|
| Do I need an AI agent framework to search companies by my filters? | May help orchestration, not evidence/coverage by itself; defer rebuild | **Postponed** |
| Does "roughly $1M–$10M" include much larger firms? | Target is still the requested band; don't silently accept out-of-band firms | **Adopted** |
| Should we search harder before building a dashboard? | Research is important but cannot consume entire deadline | **Changed priority** |
| Can we manually seed companies for dashboard testing? | I wanted actual research-written records | **Rejected** |
| Why do we need an env reviewer ID? | Multi-user reviewer/admin table better matches employees | **Changed/implemented** |
| What exactly is a reviewer versus a Supabase admin? | Reviewer = application-authorized Auth user, not project owner | **Understood/implemented** |
| Can employees use their existing email? | Yes; app doesn't create inboxes | **Adopted** |
| Should an admin approve every password reset? | Operational admin-assisted reset, no custom bypassable queue | **Simplified** |
| Is Google OAuth always limited to seven days? | No; Testing-mode external app nuance; Cal.com still selected | **Cal.com retained** |
| Why isn't the Supabase backend allowed to query? | Grants and privileged role need verification | **Investigated** |
| What's a Gmail thread ID? | Gmail conversation ID distinct from internal thread ID | **Used** |
| Do only initial emails need approval? | All outgoing exact versions do | **Implemented** |
| Should a failed AI draft still be sendable? | No; terminal failure retires unsafe pending approval | **Implemented** |
| Why does Gemini fail on basic bookkeeping questions? | Missing/branch-specific grounding and unsupported inferred service promises | **Shared reply grounding implemented** |
| What if Gemini still fails? | Needs Human; reviewer takes over, manually replies and approves | **Implemented** |
| Why was pricing working before and failing later? | Conflicting verifier, phrasing matcher and route changes; no Git version to restore | **Diagnosed** |
| Can pricing simply be discussed on a call? | Yes, standalone questions use a fixed approved template | **Implemented and live tested** |
| What if Gemini says it's human? | Must disclose AI-assistance honestly on request | **Live tested** |
| Why was return to AI blocked? | Latest inbound lacked a sent human reply after handoff, not merely a rejected draft | **Understood** |
| Should we delete Torque history and restart? | Archive/reset needed migrations; use another company instead | **Rejected/postponed** |
| Can we start fresh with Simple Sugars? | Yes; Gina chosen from saved contact list | **Implemented** |
| Why did harmless first-email questions fail? | Word whitelist in `format_first_email()` was brittle | **Fixed locally** |
| Why did "with Ledgerwise next week" not offer slots? | Too restrictive `scheduling_request()` pattern | **Fixed locally** |
| Could Ledgerwise offer an already-booked slot? | Uses provider availability and recheck; mock races depend on Cal.com conflict response | **Tested; documented caveat** |
| What if two prospects confirm one slot simultaneously? | Provider rejects second (mock 409); no global app slot lock | **Known limitation / no code change** |
| Should explicit opt-out automatically suppress contact? | Yes for safe production; current app only flagged Needs Human | **Not complete** |
| Could a human Mark as Opted Out instead? | Proposed button + DB send hold + true suppression, no message sent | **Proposed, postponed** |
| Would that button risk booking or other tests? | Requires SQL authorization changes and careful regression | **Declined under time pressure** |
| Do we need to retest every feature after each edit? | Targeted real test + full mocked regressions; don't replay all Gmail sequences every time | **Adopted** |
| Do we need every historical SQL script in GitHub? | Need reproducible **final current schema/migrations**, not guessed old SQL | **Audit planned** |
| Can we skip some deliverables to sleep? | Submit the most credible working evidence; no risky new changes | **Prioritized** |

## 14. Open items / handoff instructions to a future assistant or Codex

- Audit actual repository file inventory and currently deployed/available Supabase objects (tables/functions/RLS/grants). The original early notes are not a reliable "current schema" by themselves. Do not claim we remember every SQL command; use the **current database** as ground truth for a reproducible schema/migration export.
- Confirm Git status; at one point `ai_reply.py` was **untracked**, so do not presume earlier versions can be restored from Git. Make a clean commit and use a proper `.gitignore`, secret scan and `.env.example`.
- Check exact status of `GMAIL_SEND_ENABLED` and `CALCOM_BOOKING_ENABLED` before a public deployment. Preserve `TEST_EMAIL` delivery, existing session/callback settings, and the confirmed Cal.com booking records.
- If deploying on Render or another platform, validate production HTTPS base URL, Supabase Auth redirect URLs, Gmail OAuth authorized redirects/scopes, persistent credentials/tokens, environment variables, process command and database connectivity **without exposing secrets**. Deployment is not marked complete until actually tested.
- Prepare README, `HOW_I_WORKED.md` in my own first-person language, architecture/setup instructions, export authentic unedited AI coding sessions, Loom demo **≤10 minutes**, and final submission.
- Do **not** claim any of the following are completed: 30 confirmed fit companies, real opted-out-contact durable suppression, database-level cross-conversation reservation, or deployment/video/submission. Tell the evaluator what is currently a limitation.
- If resuming opt-out work after submission, inspect original contact vs `TEST_EMAIL`, implement pre-send database hold/true suppression with migrations and backups, and prove no unintended sends before enabling outreach to real contacts. The requested UI is a reviewer-only **Mark as Opted Out** button next to the Human Takeover controls.
- Preserve the central design contract: **no email without human approval; no real-prospect sends in demo; no booking without explicit accepted slot; no unsupported Ledgerwise service claims; no hidden unverified retries; no deletion of audit history just to restart tests**.

