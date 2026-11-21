# Project notes

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
- **Test Mode / Live Mode:**
  - Research stores real company information and contact emails in `companies`.
  - In Test Mode, FastAPI automatically copies my configured `TEST_EMAIL` into `threads.test_recipient` when creating a conversation.
  - Emails go to my test address, and I reply as if I were the prospect to test the AI.
  - In a future Live Mode, emails would go to the real `companies.contact_email`, with human approval and safety checks.
  - Only Test Mode will be enabled for the Unscale submission.
- **Gmail thread ID:**
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
- Settings contains **My Account** for admins and reviewers, showing name, email, role, and a Change Password action.
- Settings also contains **Employees** for admins only.
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

### Current progress and next steps
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