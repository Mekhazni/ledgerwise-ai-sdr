# Ledgerwise database development and Supabase schema

## Evidence and scope

A read-only request to the live Supabase REST OpenAPI endpoint returned 13 Ledgerwise tables, their exposed columns and types, required fields, and visible RPC names/signatures. No business rows or provider calls were needed. Defaults, constraints, indexes, policies, grants, triggers, and function bodies below come from the repository SQL unless explicitly described as verified live. REST metadata cannot prove the full live DDL, effective ACLs, or executed migration history. This report covers the public application schema. Supabase-managed Auth and its internal tables are dependencies.

## 1. Evolution

The project notes record a deliberate reduction from roughly ten proposed tables to six so every column could be explained. The initial design stored one contact snapshot on the company, combined sources and reasons, and put used facts and offered slots on messages. The separate source, claim, contact, and offer tables discussed in planning were omitted. There is no evidence that those proposed tables were created and later merged.

| Original table | Original purpose |
|---|---|
| companies | One prospect domain, score, outreach state, and contact snapshot. |
| reasons | Scored findings with links, quotes, and evidence quality. |
| threads | The internal conversation, Gmail link, test recipient, and ownership. |
| messages | Incoming messages, outgoing drafts, versions, citations, and slot offers. |
| suppressions | Permanent stop list keyed by normalized email. |
| bookings | Booked or cancelled calls linked to their offer and acceptance. No row means no booking has been recorded. |

Git records the base schema in f54f481 on 2026-11-19 and the later migrations together in f7a0b09 on 2026-11-21. These are commit dates. Exact creation dates and live execution order are unverified. The sequence below reflects dependencies and documented feature milestones.

| Addition | Why the six tables were insufficient | Importance now |
|---|---|---|
| reviewers | Auth identity alone did not say who could access Ledgerwise, manage employees, or approve a draft. The table links employee roles and active access to Auth. | Required for current access control. |
| pending_invitations | An invite can fail, expire, be resent, or be cancelled before an employee becomes active. Active reviewer rows could not safely represent that lifecycle. | Needed by the implemented invite flow. Separation is a design choice. |
| company_reviews | One score on companies could not preserve a reviewer's decision, rationale, identity, or prior decisions. | Required by current outreach eligibility and audit history. |
| company_contacts | One contact snapshot could not store multiple candidates, separate source evidence, or verification status. | Required for current primary/backup contact selection. |
| tomba_lookups | A call can consume quota without returning a contact. Contact rows could not represent pending, empty or uncertain lookups. | Required by the implemented per-domain reservation and global cap. |
| initial_outreach_jobs | Generate/retry/fail state must exist even when no draft was produced. A message row alone cannot distinguish old concurrent generation attempts safely. | Required for current first-email recovery and token checks. |
| reply_decisions | An inbound can result in no reply, a handoff, or a failed attempt. Its analysis job should exist independently of any outbound version. | Required by the current AI reply claim and completion flow. |

Research columns and summary JSON evolved on the company table. Human decisions gained an optional detailed criteria record and a Demo fit outcome. Primary and backup IDs were added only after multiple contacts existed. Delivery, audit, reply linkage, and Gmail metadata extended messages. Booking and takeover added functions around existing tables. No original application table was renamed or removed by the checked-in SQL. Legacy company contact fields remain.

Key replacement chains include research saving through allow_partial_company_research.sql, first-draft start/refresh/completion through add_outreach_review_and_send.sql, Gmail import through fix_gmail_reply_import.sql, and ownership and reviewer booking through add_reviewer_scheduling.sql. Earlier additive schema changes remain prerequisites. README.md lists the full installation order.

## 2. Current tables and columns

PK means primary key, FK means foreign key, and timestamptz means timestamp with time zone. Unless stated otherwise, a field is required and has no default. A generated bigint ID is GENERATED ALWAYS AS IDENTITY in the repository. Constraints/defaults below are repository definitions. The live column inventory matched.

### companies

Parent of reasons, reviews, contacts, threads, lookup records and its first-email job. main.py's read_companies reads it. save_researched_company creates it, save_outreach_contacts assigns candidates, and booking/opt-out RPCs update sales state. Example: researched storefront data is saved once under its clean domain.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, stable company identifier. |
| name | text, display and draft context. |
| website | text unique, clean lowercase domain for deduplication and lookup. The check excludes www. |
| score | integer default 0, research ranking. |
| status | text default new, sales state. Check allows new, qualified, contacted, replied, booked, opted_out. Allowed states do not mean every workflow writes every intermediate state. |
| contact_name | nullable text, name found during research. |
| contact_role | nullable text, role found during research. |
| contact_email | nullable text, legacy/research email field. Delivery uses the configured test inbox. |
| created_at | timestamptz default now(), creation history. |
| qualification_status | nullable text, AI classification. Allows needs_more_research, poor_fit, potential_fit, fits_profile_no_recent_signal_found, qualified_for_review. Human decisions are separate. |
| missing_information | text[] default empty, tracks research gaps. |
| research_errors | jsonb default empty array, research diagnostics. |
| research_summary | nullable jsonb object, structured findings and score breakdown. |
| primary_contact_id | nullable bigint FK to company_contacts.id, deferred, reviewer-selected primary. |
| backup_contact_id | nullable bigint FK to company_contacts.id, deferred, optional backup requiring a distinct primary. |

### reasons

Evidence belongs to one company. save_researched_company writes it, read_companies displays it, and initial_outreach.py's saved_facts selects eligible evidence for a draft. Example: verified online sales earn points supported by a storefront quote.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, evidence identity. |
| company_id | bigint FK to companies.id, evidence owner. |
| criterion | text checked against hiring, funding, new_channel, location, size, ecommerce, contact. |
| finding | text, reviewer-readable conclusion. |
| points | integer default 0, score contribution. Unknown evidence must score zero. |
| source_url | nullable text, underlying source link. |
| quote | nullable text, supporting source text. |
| evidence_status | text, verified, estimate or unknown. Unknown may omit both source fields. Otherwise both must be nonblank. |
| created_at | timestamptz default now(), evidence timestamp. |

### threads

Conversation parent for messages and bookings. finish_initial_outreach stages a thread, record_initial_send saves its Gmail link, change_conversation_owner controls ownership, and conversations_page and inbox sync read it. Example: a first send associates a Ledgerwise conversation with the Gmail thread returned by the provider.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, internal conversation ID. |
| company_id | bigint FK to companies.id, prospect association. |
| test_recipient | text, configured test recipient staged for that thread. |
| status | text default ai_handles, also allows needs_human or human_took_over. AI ownership requires owner to be null. |
| owner | nullable uuid FK to auth.users.id, ON DELETE SET NULL, the human owner. |
| handoff_reason | nullable text, why human review or ownership was needed. |
| gmail_thread_id | nullable unique text, Gmail conversation ID after confirmed send. |
| created_at | timestamptz default now(), creation time. |
| demo_only | boolean default false, sticky demo restriction applied by triggers. The current app also enforces test-only delivery for other threads. |


### messages

All inbound emails and outbound versions. Written by first-draft completion, Gmail import, AI reply completion, review/manual composition and record_initial_send. Read through email_drafts_page, conversations_page, load_reply_parent and scheduling/meeting queries. Example: a reviewer edit retires the old outgoing version and inserts a new draft that needs fresh approval.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, message/version identity. |
| thread_id | bigint FK to threads.id, conversation membership. |
| direction | text, in or out, constrained jointly with status. |
| subject | text, displayed subject and provider-threading context. |
| body | text, plain-text email or draft. |
| status | text, received for inbound and draft/approved/sent/rejected for outbound. |
| version | integer default 1, positive, copy version. |
| approved_version | nullable integer, equals version when present and required for approved/sent outbound. |
| facts_used | jsonb default empty array, cited-evidence snapshots. |
| offered_slots | jsonb default empty array, exact choices carried by a scheduling draft or sent offer. |
| gmail_message_id | nullable unique text, provider lookup and deduplication key. |
| created_at | timestamptz default now(), local history and ordering fallback. |
| initial_outreach | nullable jsonb object for outbound only, intended contact and first-email generation/personalization snapshot. |
| review_events | jsonb default empty array, audit of versions, approvals, ownership and selected provider/booking events. |
| send_state | nullable text, sending/sent/failed/unknown, separate provider outcome. |
| actual_recipient | nullable text, actual authorized destination, currently TEST_EMAIL. |
| send_error | nullable text, delivery failure/uncertainty detail. |
| sent_at | nullable timestamptz, observed send time and offer-history anchor. |
| inbound_email | nullable jsonb object for incoming only, sender/recipient and email reference headers. |
| received_at | nullable timestamptz, source receive time for newest-inbound checks. |
| reply_to_id | nullable bigint self-FK to messages.id, inbound parent of reply versions. Prior version references can appear separately in audit events. |

### suppressions

Stop list read by the initial and reply authorization RPCs, written by Gmail import and the AI finish suppression branch. Example: clear opt-out inserts normalized addresses with the incoming trigger and marks the company opted out. The shared-inbox limitation is explained below.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, suppression identity. |
| email | text, unique, nonblank and lowercased/trimmed, matched during send authorization. |
| reason | text, why communication was stopped. |
| trigger_message_id | nullable bigint FK to messages.id, links the cause if available. |
| created_at | timestamptz default now(), decision timestamp. |

### bookings

Confirmed calls and supporting message links. scheduling_draft calls reservation and recording RPCs. meetings_page reads booked rows and derives pending offers separately from sent messages. Example: a Cal.com success creates a booked row and adds meeting URL/end time to audit data when supplied.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, local meeting identity. |
| thread_id | bigint FK to threads.id, associated conversation. |
| start_time | timestamptz, accepted start, timezone-aware. |
| calcom_booking_id | nullable unique text, provider UID, required by the recording RPC for successful bookings. |
| status | text, booked or cancelled. Pending offers are stored on messages. |
| acceptance_message_id | bigint unique FK to messages.id, one recorded booking per accepting message. |
| offer_message_id | bigint FK to messages.id, original sent offer. |
| created_at | timestamptz default now(), local recording time. |

### reviewers

Ledgerwise access profile. reviewer_profile and signed_in_account read it. update_employee, complete_employee_invitation, and delete_employee control lifecycle. Example: completing a valid invite activates a previously invited Auth user.

| Column | Type and purpose |
|---|---|
| user_id | uuid PK/FK to auth.users.id, ON DELETE CASCADE, one app profile per Auth identity. |
| full_name | nonblank text, display name and audit snapshot source. |
| phone | nullable text, optional employee phone. |
| role | text, admin or reviewer, permission level. |
| active | boolean default false, denies access until enabled. |

### pending_invitations

Invite lifecycle separate from active access. invite_employee, send_employee_invitation, change_invitation, resend_invitation and cancel_invitation update it. complete_employee_invitation reads and transitions it. Example: a delivery failure leaves a failed record for retry.

| Column | Type and purpose |
|---|---|
| id | uuid PK default gen_random_uuid(), invitation identity. |
| user_id | nullable unique uuid FK to auth.users.id, ON DELETE SET NULL, ties invite to recipient account. |
| email | unique nonblank text, lowercase/trimmed, invite destination. |
| full_name | nonblank text, profile snapshot to activate. |
| phone | nullable text, optional profile phone. |
| role | text, admin or reviewer, intended role. |
| status | text, sending/awaiting/failed/cancelled/accepted. |
| last_attempt_at | timestamptz, send/resend state. |
| expires_at | nullable timestamptz, deadline checked before activation. |

### company_reviews

Human qualification history independent of research scoring. record_review calls record_company_review. read_companies, outreach, Tomba, reply and booking eligibility read it. Example: a Demo fit review inserts a decision and triggers a sticky flag on that company's threads.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, order used to select latest company review. |
| company_id | bigint FK to companies.id, reviewed company. |
| reviewer_user_id | nullable uuid FK to auth.users.id, ON DELETE SET NULL. |
| reviewer_name | text, name snapshot preserved after account deletion. |
| reviewed_at | timestamptz default now(), decision time. |
| decision | text, confirmed_fit/demo_fit/rejected/needs_more_research. |
| notes | text, required in storage but can be empty. Max 500 check is NOT VALID for old rows while enforced on new writes. |
| criteria | nullable jsonb, older detailed review criteria. Original object/key and confirmed-fit checks still apply when present. |


### company_contacts

Saved business candidates, read by read_companies, contact assignment and first-draft orchestration. save_researched_company can persist supplied contacts and finish_tomba_lookup writes provider results. Example: Tomba returns a candidate, then a reviewer explicitly chooses the primary contact.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, candidate identity referenced by company assignments and first-email jobs. |
| company_id | bigint FK to companies.id, candidate's company. |
| email | nonblank text, case-insensitive unique within the company through an expression index. |
| name | nullable text, candidate person. |
| role | nullable text, title. |
| contact_type | text, finance/founder/coo/general_inbox/needs_review. First three require name and role. |
| sources | jsonb array, published records need at least one source. Provider-reported rows can have an empty array. |
| publication_status | text default published, published or provider_reported. |
| delivery_status | text default unverified, unverified/verified/undeliverable. |
| provider_status | nullable text, provider disposition. |
| provider_confidence | nullable integer, 0–100, confidence reported by provider. |

### tomba_lookups

Reservation ledger read by email_drafts_page and written through lookup_company_contacts and its reserve/finish RPCs. Example: an uncertain lookup can consume a reservation even when zero contacts were saved.

| Column | Type and purpose |
|---|---|
| id | bigint generated PK, reservation identity. |
| company_id | bigint FK to companies.id, requesting company. |
| domain | unique text, prevents another lookup for the same domain. |
| requested_by | nullable uuid FK to auth.users.id, ON DELETE SET NULL, requesting actor. |
| requested_at | timestamptz default now(), reservation time. |
| status | text default reserved, reserved/completed/no_results/failed/uncertain. |
| preferred_contact_id | nullable bigint FK to company_contacts.id, ON DELETE SET NULL. Optional provider preference. The current app passes no preference and uses explicit company assignments. |

### initial_outreach_jobs

One current first-email job per company. Started, refreshed and completed by the corresponding initial outreach RPCs and read by email_drafts_page. Example: Retry draft reclaims the failed job using the existing selected contact and a fresh token.

| Column | Type and purpose |
|---|---|
| company_id | bigint PK/FK to companies.id, ON DELETE CASCADE, one job per company. |
| contact_id | bigint FK to company_contacts.id, ON DELETE CASCADE, generation input and recheck. |
| token | uuid default gen_random_uuid(), prevents a stale worker from saving after another attempt. |
| state | text, generating/ready/failed. |
| started_at | timestamptz default now(), interrupted-job detection. |
| failure_reason | nullable text, visible retry diagnostic without retaining rejected draft copy. |

### reply_decisions

One decision or generation job per inbound. run_ai_reply calls claim_ai_reply and finish_ai_reply; conversations_page and reply_generation_response read it. Example: a terminal validation failure stores a reason and hands the thread off without saving a new outbound draft.

| Column | Type and purpose |
|---|---|
| incoming_id | bigint PK/FK to messages.id, ON DELETE CASCADE. One current decision per incoming message. |
| state | text, generating/failed/ready. Ready includes no-action or human decisions. |
| token | uuid default gen_random_uuid(), active attempt identity. |
| started_at | timestamptz default now(), retry timeout reference. |
| decision | nullable jsonb, action, reason and routing snapshot. Outgoing copy is stored separately. |
| error | nullable text, generation/verification error for retry. |

## 3. SQL logic and security

| Current function | What it protects or records |
|---|---|
| update_employee | Active admin check, reviewer-table lock, and last-admin protection for edits/deactivation. |
| complete_employee_invitation | Locks invitation and reviewer state, checks expiry and a matching confirmed Auth user with password, then activates access. |
| delete_employee | Active admin, typed confirmation email and last usable admin checks before account/invitation deletion. FK actions preserve independent conversation and audit history. |
| save_researched_company | Saves company, reasons and supplied contacts together. Requires verified online product-sales evidence and avoids overwriting an existing domain. |
| record_company_review | Adds a new decision for an authorized active reviewer, preserving name and history. |
| reserve_tomba_lookup | Locks the lookup ledger before spending credits. Checks reviewer, fit, domain deduplication and the 23-entry cap. |
| finish_tomba_lookup | Resolves a reserved lookup and persists returned candidates and metadata. |
| save_outreach_contacts | Locks company, checks eligibility, candidate membership and delivery status, then saves distinct primary/backup selections. |
| start_initial_outreach | Checks reviewer and current contact/send state, reuses saved assignment for retries, and claims a generation token. Existing sent first outreach blocks a new initial send. |
| refresh_initial_personalization | Claims a retry/refresh token for the latest eligible draft/rejected version; rejects active jobs and sent/approved conflicts. |
| finish_initial_outreach | Locks company; checks token, contact and fit. Records failure or writes the next draft version and retires old pending copy. |
| review_initial_message | Checks current exact version, reviewer, fit and contact. Edits insert a new version. Approval checks suppression and claims sending before Gmail is called. |
| record_initial_send | Records only the claimed exact version as sent, failed, or unknown. Saves provider IDs on confirmed success. |
| import_gmail_reply | Validates the Gmail thread and test sender, deduplicates by Gmail ID, retires stale drafts, and processes clear opt-outs. |
| reply_context_allowed | Requires latest incoming, AI ownership, fit, Gmail link, and unsuppressed context. |
| claim_ai_reply | Locks company and claims a token for the incoming reply; checks active generation and existing draft/approval state. |
| finish_ai_reply | Rechecks token/context, saves the decision, retires superseded drafts, and inserts a new draft only if copy exists. Also handles suppression or Needs Human. |
| manual_reply_context_allowed | Requires assigned owner plus current inbound, eligibility, Gmail and suppression checks. |
| review_reply_message | Guards exact versions, latest inbound, AI/manual ownership, duplicates, active generation, suppression and required reply headers. |
| change_conversation_owner | Records takeover/return in audit events. Return checks ownership and current context. Pending replies or unresolved handoff without a sent latest reply can block it. Active booking attempts also guard ownership changes. |
| compose_manual_reply | Allows an assigned reviewer to save a new draft for the current inbound when no conflicting draft/send exists. |
| reopen_mistaken_ai_handoff | Reopens two recognized mistaken handoff intents only, preserving the exact original decision in history. |
| finish_scheduling_reply | Reuses AI reply completion and saves the offered slots on the resulting message. |
| reserve_calcom_booking | Matches a current inbound and exact slot to a previously sent offer. Checks AI token or reviewer ownership, test recipient, and thread-local duplicate state. Records attempt before provider call. |
| record_calcom_booking | Reads the reservation audit, stores a provider-confirmed booking with offer/acceptance links, and records provider metadata and company status. |
| compose_reviewer_scheduling | Saves a manual offer or confirmation draft under the existing ownership and version rules. |


### Triggers, indexes, authorization and locks

- keep_demo_thread_test_only runs before thread inserts/updates. It inherits Demo fit on a new thread, keeps a demo flag true, and stops demo threads from moving to another company. mark_company_demo_threads runs after a Demo fit review and flags existing threads for that company. Both trigger functions are defined in add_demo_fit.sql.
- company_reviews_company_history_idx supports latest-decision lookup by company and descending ID. company_contacts_company_email_idx is unique on company and lowercased email. reply_message_version_uidx makes reply versions unique per inbound parent. outreach_initial_send_subject_idx is unique on normalized initial subjects while sending, sent, or unknown. This reduces accidental Gmail grouping when the test inbox is shared.
- Primary and unique keys also protect domain, normalized suppression address, Gmail thread/message IDs, invitation email/user, lookup domain, provider booking UID, and one booking per acceptance. None of the repository indexes makes a slot unique across different conversations.
- Table checks are listed in the inventory. They enforce data shape and invariants such as positive versions, approved version equality, valid direction/status combinations, normalized addresses, and distinct contact assignments. Most nested JSON key rules are enforced by app/RPC construction.
- Supabase Auth establishes the identity. FastAPI then checks an active reviewer profile using server-side access. The privileged RPCs independently check role or ownership as appropriate. secure_employee_access.sql removes the original Read own reviewer access policy and changes reviewer access to a backend-only path.
- All 13 application tables enable RLS in the repository. The original six grant service_role SELECT/INSERT/UPDATE/DELETE, and secure_employee_access.sql removes public/anon/authenticated access to them. The added tables have narrower service_role grants: SELECT/INSERT for reviewers and company_contacts, SELECT/INSERT/UPDATE for pending_invitations, and SELECT for company_reviews, tomba_lookups, initial_outreach_jobs and reply_decisions. Controlled writes use the designated functions.
- Privileged entry points use SECURITY DEFINER with an empty search path and qualified table names. Files revoke broad EXECUTE and grant the intended RPCs to service_role. Later CREATE OR REPLACE operations preserve the existing signature's privileges. Helper function presence in REST metadata does not establish effective privileges or function ownership.
- Locks and rechecks matter because a browser can be stale and two workers can act at once. The lookup ledger lock protects the cap; company locks serialize important draft/reply/booking decisions; reviewer and invitation locks protect account lifecycle. Tokens protect against stale background completions. The first-draft jobs permit stale recovery after three minutes, while the reply job claim uses a five-minute window.
- Gmail runs outside the database transaction. Approval claims the exact draft first and the result is recorded afterward. A known failure can return to draft, while unknown delivery keeps its approved version and blocks automatic resend. A provider timeout or an interrupted database write cannot prove Gmail did not deliver. For an AI-owned reply, FastAPI also checks for a failed regeneration before calling Gmail.
- Booking also spans a database reservation and an external call. A prior attempt is durable even if Cal.com times out. Human or AI booking uses the same sent-offer/current-acceptance checks. Competitors in separate threads may both reach Cal.com after separate availability reads. The app relies on Cal.com to reject that race and flags a booking-time conflict or uncertain outcome for reconciliation.

## 4. Database workflow examples

| Workflow | Database operations in order |
|---|---|
| 1. Admin creates or invites an employee | Bootstrap creates an Auth user and an active admin reviewers row. Routine invite uses the admin-gated FastAPI endpoint, records pending_invitations, calls Supabase Auth, and records delivery state/expiry. Password setup enables complete_employee_invitation to activate reviewers and mark the invite accepted. |
| 2. Research finds a company | start_research authorizes an admin and runs research. research_payload builds company/evidence data. save_researched_company atomically inserts companies, reasons, and any supplied contacts. A duplicate domain returns existing. Human review subsequently appends company_reviews. |
| 3. Contact discovery saves candidates | reserve_tomba_lookup checks reviewer, fit and budget and records a reservation before Tomba. finish_tomba_lookup writes disposition and company_contacts. save_outreach_contacts saves the explicit primary and backup IDs. |
| 4. Gemini produces a first draft | start_initial_outreach claims a company/contact job, Python writes/verifies copy, and finish_initial_outreach checks the token and saves a thread and draft version. A failed candidate instead sets the job failure reason. |
| 5. Reviewer edits, approves and sends | Edited first copy is verified before review_initial_message retires the old row and saves the new version. The approval transaction checks suppression and claims that exact version. Gmail sends to TEST_EMAIL. record_initial_send writes the result and attaches the Gmail thread on success. |
| 6. Gmail receives a reply | Manual sync reads known threads and tests sender/thread binding. import_gmail_reply writes a received messages row with provider IDs, timestamp and headers, deduplicates, and retires stale drafts. |
| 7. Gemini generates an ordinary reply | claim_ai_reply reserves a token for current inbound. FastAPI checks scheduling first and then runs the reply generator where appropriate. Content is checked and may receive one correction. finish_ai_reply records the decision and saves a draft if copy passes. Recognized standalone pricing uses approved fixed copy. |
| 8. Reply is rejected and regenerated | review_reply_message records rejection. Regeneration claims the current inbound/version. Success inserts a new version. Terminal failure records Needs Human and retires older pending copy. Temporary provider errors remain retryable and failed regeneration is blocked from sending. |
| 9. Human Takeover | A flagged thread saves handoff reason. An authorized reviewer explicitly takes ownership. compose_manual_reply saves a draft, review_reply_message enforces approval, and change_conversation_owner applies the return conditions. |
| 10. Prospect requests a meeting | scheduling_draft examines the inbound and sent offers. The Cal.com client retrieves availability. No booking row is created yet. |
| 11. Cal.com returns available slots | Ledgerwise can offer up to three available choices. finish_scheduling_reply or compose_reviewer_scheduling stores them in messages.offered_slots with the outbound draft. Sending the offer requires approval. |
| 12. Prospect confirms a slot | The app matches an explicit acceptance to the exact sent offer, rechecks availability, reserves the operation and submits to Cal.com with TEST_EMAIL. On confirmed success, record_calcom_booking writes bookings and the audit trail. The confirmation email is another draft for approval. Phone is optional. |
| 13. Duplicate or conflicting booking attempt | Same-thread claims are blocked by the reservation checks. If the recheck finds the slot gone, the app drafts alternatives. A cross-thread race may reach Cal.com twice. A provider rejection or uncertain result flags the losing attempt for review without a confirmation draft. |
| 14. Prospect opts out | A clear Gmail opt-out can suppress both the original business email and shared TEST_EMAIL, then mark companies.status opted_out. An inferred opt-out can stay at Needs Human without a suppression entry. Approval checks existing suppression, while a human-review flag by itself is not a permanent send block. The booking row is unaffected by these SQL paths. |

## 5. Historical decisions and limitations

- The small original schema separated core entities but avoided extra tables for evidence sources, used claims, and slot offers. An early single-reviewer environment setting was replaced as the design evolved to multi-employee roles. The project notes say the admin-approval queue for password resets was dropped. Those design proposals do not establish that prior SQL tables or functions ever existed.
- The seven added tables solve distinct needs, especially history, state before a provider call, and operations that may fail without producing an output row. Transactional checks are necessary where UI validation cannot prevent stale versions or duplicate work.
- The chronological replacement chain is important. The final checked-in definitions are in allow_partial_company_research.sql for research saving, add_outreach_review_and_send.sql for first-draft start/refresh/finish and delivery state, fix_gmail_reply_import.sql for Gmail import, add_human_takeover.sql for reply review, and add_reviewer_scheduling.sql for ownership and the reviewer-aware booking reservation.
- The proposed reviewer-controlled contact-only opt-out was postponed. There is no checked-in migration, button implementation, or visible live RPC for that feature. The earlier clean-thread archive plan is likewise not present in the application SQL. No live migration ledger was available to prove which experimental SQL statements may have run temporarily.
- Current gaps include shared-test-inbox suppression, possible opt-outs that remain only flagged, provider-dependent cross-thread booking conflicts, and no automatic import of external cancellations. Human-written copy relies on reviewer judgment for content. Failed candidate bodies and intermediate verifier decisions are not fully retained.

## 6. Reproducibility

**The repository appears to contain the SQL needed for all 13 application tables and the RPCs the backend uses. Exact live parity and a fresh installation remain unverified.**

- I found no missing application table, exposed column, or backend-called RPC when comparing repository definitions with the live service-role REST OpenAPI response. The live response also advertises rls_auto_enable, which is absent from the repository. Its origin, body, grants and any attached event trigger cannot be determined from the metadata. No backend call to it was found.
- The repository defines a seven-argument reserve_calcom_booking updated by allow_optional_booking_phone.sql and an eight-argument overload added by add_reviewer_scheduling.sql. Live REST advertises the eight-argument form. The OpenAPI listing alone cannot conclusively verify both overloads.
- The SQL needs Supabase's Auth schema and roles. It does not recreate project data, Auth users/passwords, the first active admin record, Supabase Auth configuration or credentials for external providers.
- The ordered list in [README.md](README.md) preserves later function replacements. add_human_takeover.sql and add_calcom_booking.sql can run in either order after add_ai_reply_drafts.sql. Both must precede add_reviewer_scheduling.sql. seed_company.sql is optional sample data.
- Full verification would need a read-only PostgreSQL catalog comparison with a fresh disposable project, including defaults, constraints and FK actions, indexes, function bodies and overloads, triggers, RLS, grants and default ACLs. No SQL was run against the database, and this report makes no claim that the repository migrations have been replayed on a fresh project.

