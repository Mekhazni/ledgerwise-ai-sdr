# Project notes

## Plan changes
- Planned Google Calendar, switched to Cal.com because the Google login can expire after 7 days and the site must stay up 2 weeks.
- Moved login and deployment after the research test on 3 companies, because research is the riskiest part.
- Simplified the database from 10 tables to 6, so I can explain every column.
- Limited `bookings.status` to `booked`/`cancelled`. No booking row means no call yet.

## Where Codex got something wrong or went too fast
- Started writing login and deployment files before the tables and the research test. I declined and set the order.
- Its first schema had no permission for the backend. I noticed because I had unticked the Supabase grant box, and Codex confirmed it.

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