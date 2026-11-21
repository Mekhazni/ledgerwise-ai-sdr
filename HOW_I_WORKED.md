# How I worked on Ledgerwise

I built a supervised outreach tool for Ledgerwise, a fictional US bookkeeping firm serving e-commerce brands. I worked from Unscale’s brief and planned a complete demonstration from company research and qualification through reviewed outreach, replies, and booked calls. The deliverables included a public repository, a hosted app, the unedited AI sessions, this work log, and a short Loom walkthrough. I tested with real company research and controlled inboxes. I did not reach 30 human-confirmed fits, and permanent opt-out suppression is unfinished.

## 1. Tools I used and why

| Step | What I used and why |
|---|---|
| Research | I used Tavily to find pages, HTTPX to fetch them, and Beautiful Soup to read their text. Tavily Extract helped when ordinary fetching failed. Gemini 3.1 Flash-Lite extracted structured findings, and Python checked whether each quote actually appeared in the source. I added Groq GPT-OSS 120B as an optional research fallback for Gemini service errors or read timeouts. |
| Qualification and contacts | Gemini extracted structured findings, Python applied the scoring rules to the saved evidence, and human reviewers made the final fit decisions. I dropped in-house email extraction after Hello Cake's examples were saved as contacts. I considered Hunter and explored Apollo for discovery. I kept Tavily for research and used Tomba for manual contact lookup after review. I chose the primary contact myself. A sourced address is different from a delivery-verified address. |
| Email writing and replies | Gemini wrote personalized first emails and ordinary replies. The app checked prospect claims against saved evidence and Ledgerwise claims against approved facts. Gmail API and OAuth let me send approved drafts, import replies manually, and keep each conversation in its own Gmail thread. I replaced Gemini's standalone pricing answer with approved wording after live verification repeatedly contradicted itself. |
| Booking | I chose Cal.com for real availability and booking, with Google Calendar connected and Cal Video for the call. I moved away from direct Google Calendar integration because a test-mode OAuth credential could expire during the required two-week demo period. |
| Platform and hosting | I used FastAPI, Supabase Postgres and Auth, and plain HTML, CSS, and JavaScript. This kept the app within a stack I could explain. I chose Render’s free web service for the hosted demonstration and kept the existing Supabase project. |
| AI tools | I used ChatGPT to brainstorm, understand technical concepts, compare approaches, analyze failures, turn informal ideas into clear Codex instructions, and plan testing, deployment, and documentation. I consulted Claude for planning and Git decisions. Codex inspected the codebase in VS Code, implemented features, investigated errors, proposed fixes, and ran tests. I benchmarked local Ollama models and kept Gemini as the research primary because it did better on supported fit findings. |

## 2. My plan and how it changed

I started with a three-company research script and a JSON file so I could inspect the sources before connecting a database. I looked for US e-commerce product companies in the target revenue and headcount ranges, with active hiring or recent funding or expansion as reasons to reach out. Gemini extracted findings into structured JSON. I used Python to check the quotes and apply the scoring rules. Later I added dashboard batches of up to ten, with automatic discovery or manually supplied domains. Revenue and headcount often remained unknown, so I kept incomplete product businesses visible for later review and kept the Python-generated scores separate from the final decisions made by reviewers. Demo fit let me test outreach without counting those companies toward the target.

Direct fetches sometimes returned HTTP 403 even for pages I could open in a browser. I confirmed that Tavily Basic Extract could read the Lulus homepage and added it as a bounded fallback. Gemini outages and timeouts also interrupted batches, so I added a limited Groq fallback for research. I compared models using the same saved excerpts without new Tavily calls. Some results were outside the target, including companies that were too large or sold software to e-commerce brands. Public revenue and headcount gaps and the time needed for human verification kept me below 30 confirmed fits.

I connected the pilot to FastAPI and Supabase, then built the six dashboard sections: Overview, Companies, Email Drafts, Conversations, Meetings, and Settings. Supabase Auth handles sign-in, and FastAPI checks active reviewer access and roles. Admins manage employees and start research. Admins and reviewers can inspect evidence, record a human fit decision, and choose a primary and optional backup contact from manually requested Tomba results. A provider confidence score did not prove delivery.

I built evidence-based first drafts next. Reviewers can edit, reject, or approve the exact version, and earlier versions remain in history. Gmail sends only to my test inbox. Manual sync imports matching replies from known threads and deduplicates Gmail message IDs. Gemini handles questions about services, objections, and AI identity, with an approved fixed answer for standalone pricing. When the AI cannot answer safely, the reviewer can take over and draft a response for approval. Returning to AI depends on the current conversation state.

For booking, I used Cal.com to retrieve real slots. The reviewer approves the offer before Gmail sends it. The prospect must explicitly accept a previously offered slot, and the app rechecks availability before creating a booking. Reservation checks help prevent duplicate bookings, while unclear provider results require a human check before retrying. Meetings appear in the dashboard and the conversation history. Repeated research tuning was taking time away from the rest of the workflow, so I stopped optimizing discovery and used controlled conversations to test the whole path.

I started with six tables for companies, evidence, threads, messages, suppressions, and bookings. The public application schema grew to 13 tables as I added roles and invitations, human review history, multiple contacts, lookup limits, first-draft attempts, and AI reply decisions. The original tables also gained fields to support approval, Gmail threading, and booking safeguards.

## 3. Rules and context I gave Codex

In AGENTS.md I asked Codex to plan first, work in small steps, explain each change, and enforce client rules in code. The target was US e-commerce brands with $1M–$10M in revenue and 5–50 employees. The contact could be whoever runs finance, a founder, or a COO.

I required sourced prospect claims, approved Ledgerwise facts, no quoted prices, honest AI disclosure, and a human handoff when the AI was unsure. I required opt-outs to stop future outreach and kept keys in environment variables. Every Ledgerwise email, including a human reply or booking confirmation, required approval of its exact version and went only to my controlled test inbox. Cal.com used that inbox as the attendee and could issue its own notification.

I defined the requirements, proposed ideas, questioned technical choices, and tested the results myself. Before approving a fix, I reviewed Codex's proposal and made the final decision. As more features started working, I settled into a plan → investigate → approve → implement → test routine so a fix in one area would leave the rest of the workflow intact.

## 4. Where things went wrong

- A quote about shipping to the US passed the text match even though it could not prove US headquarters. Reading the source taught me to check what a quote proves as well as whether it exists.
- On Torque Fitness, Gemini invented capabilities such as integrations and reporting. On Simple Sugars, its verifier first demanded missing pricing details and then rejected the correction for acknowledging they were unknown. I replayed the saved questions without sending anything. We clarified the boundary between general bookkeeping and confirmed services, kept human handoff for failed checks, and switched standalone pricing to one approved answer.
- A first-email rule rejected ordinary closing questions such as “Would you be open to hearing more?” Gmail line wrapping also broke a next-week scheduling request, and the app initially missed “Option 1 works for me. Please book it.” Replaying the exact messages exposed failures the mocks had missed. We removed the first-email phrase list while keeping the ban on specific times and booking links. We fixed scheduling to handle wrapped text and match explicit option numbers against the sent offer.
- Some initial drafts failed because natural line wrapping did not match the required email structure. I had Codex fix harmless whitespace before validation while preserving the words and claims. When a reply failed to appear in Conversations, we traced thread matching and reply handling instead of assuming Gmail had lost it. I kept exact-version approval even when a draft needed another attempt.
- The Simple Sugars opt-out was flagged for human review without permanently suppressing the business contact. I postponed the proposed migration and changes to send authorization because they could disrupt tested workflows late in the project. That decision leaves a real safety gap. I kept the application test-only.

## 5. GitHub, Render, and Google OAuth

I prepared the GitHub repository in stages and connected its main branch to Render. I deployed Ledgerwise at [ledgerwise-ai-sdr.onrender.com](https://ledgerwise-ai-sdr.onrender.com/) and configured the service’s environment variables for the existing Supabase project and integrations. I kept credentials out of Git and retained the reviewer sign-in and server-side dashboard checks. The code and branding changes were pushed to GitHub. The complete hosted workflow still needs a final pass.

I chose Render Free to make Ledgerwise publicly accessible without paying for hosting during development and evaluation. On November 21, 2026, I checked the Render billing dashboard. It showed 1.37 / 750 free instance hours, 0 MB / 5 GB of bandwidth, 1 / 25 services, 1 / 500 pipeline minutes, and 0 / 2 custom domains. That was a snapshot of usage at the time. Supabase, Gmail, Gemini, Tavily, Tomba, and Cal.com have separate limits that the Render dashboard does not measure.

On Render Free, the service spins down after about 15 minutes of inactivity. The first request after that may take around a minute while the service starts. I treat that delay as normal idle behavior. On its own, it does not indicate that the app failed or was deleted. I decided to keep the free plan for Unscale because usage was low, with the understanding that uninterrupted availability during the evaluation is not guaranteed.

Gmail initially used Google OAuth in External / Testing mode. I learned that its refresh token could expire after seven days, halfway through Unscale’s two-week review period even if issued on submission day. I chose to move the OAuth app to Production rather than plan on repeatedly reauthorizing it.

The branding setup needed a public homepage and privacy policy. Codex added About, Privacy, and Terms pages, linked them from login, and kept the dashboard protected. Search Console was checking the root URL, but the verification tag only appeared on About. We added the tag to the signed-out login page reached from the root redirect. I verified ownership in Search Console, configured the branding URLs and authorized domain, and moved the Google OAuth publishing status to In Production. Publishing status is separate from Google’s scope review.

I reauthorized the dedicated Gmail account in OAuth Playground using my own OAuth client and requested both Gmail scopes. Codex tested the new refresh token locally without sending email or syncing the inbox. Refresh succeeded, the Gmail profile returned HTTP 200, the mailbox matched the configured sender, and both `gmail.readonly` and `gmail.send` were granted. Newly issued Production tokens are outside the Testing-mode seven-day rule, but can still become invalid. The local check did not prove that the running Render instance loaded or could use that token.

## 6. How I checked the agent

| Live scenario | Result |
|---|---|
| Torque Fitness and Simple Sugars first outreach | **Pass.** Approved versions reached my test inbox after earlier draft failures. |
| Simple Sugars service question | **Pass.** A real Gemini reply separated general bookkeeping from what Ledgerwise offers. I reviewed and approved it. |
| Simple Sugars standalone pricing | **Pass.** The approved wording went through human approval and test delivery without scheduling a meeting. |
| Torque Fitness AI disclosure and in-house objection | **Pass on retest.** The disclosure was honest, and the objection reply passed after earlier unsupported wording was caught. |
| Simple Sugars unconfirmed services | **Pass.** The app flagged Needs Human. I used a manual reply and returned the thread to AI assistance. |
| IQBAR and Simple Sugars scheduling | **Pass.** Two real test bookings appeared in Meetings after specific offered slots were confirmed. |
| Simple Sugars opt-out | **Fail.** The app flagged it for review without permanently suppressing the original contact. The booking stayed in place. |

**Automated tests.** The earlier workflow suite passed 96 tests. The latest local run passed 100, including four tests for the public pages, footer links, root verification tag, and signed-out dashboard protection. The workflow suite substitutes controlled responses for Supabase, Gmail, AI providers, and Cal.com. It covers cases such as stale approvals, duplicate sends, and booking conflicts without proving live database concurrency or provider behavior.

The live results above came from controlled Gmail and Cal.com tests. Search Console ownership verification and the local Production-token diagnostic were separate live checks. Full hosted testing remains pending.

## 7. What it costs to run

- **Actual spend.** I developed within free-tier constraints. I checked Render usage as described above. I did not reconcile bills across all providers, so I cannot report a total.
- **Free-tier usage.** I used Tavily, Gemini, and Tomba for research or contacts and tested Groq. Supabase, Gmail, and Cal.com supported the controlled prototype. I did not measure total usage across those services.
- **Provider quotas recorded during testing.** Gemini allowed 15 requests per minute, 250,000 tokens per minute, and 500 requests per day. My Groq account showed 30 requests per minute, 1,000 requests per day, 8,000 tokens per minute, and 200,000 tokens per day. These are historical figures from development. I would recheck current allowances before sizing a larger workload.
- **Application limits.** I capped each research batch at 40 Tavily searches, 60 distinct URLs, and 20 model attempts across both model providers. Tomba has a separate app cap of 23 manual lookups, based on my remaining credits. These caps are separate from provider quotas and measured usage.
- **Future operating costs.** I did not measure enough usage to estimate a monthly cost. I would track searches and model calls per confirmed fit, lookups per usable contact, and reply volume, then combine those figures with projected hosting and database usage.

## 8. What I would do next with more time

### 1. Improve company discovery and qualification

I would improve how the app finds and filters companies, collects evidence, and checks whether a company meets the target criteria. I would look for more reliable ways to verify annual revenue, employee count, US location, and recent business activity. I would measure how many companies reviewers confirm as genuine fits and keep unknowns visible so reviewers can see where more research is needed.

### 2. Improve AI-generated emails and replies

I would make first emails more personal, natural, and consistent while checking that each factual claim comes from verified information. For replies, I would test whether the AI understands what the prospect is asking, responds appropriately to questions and objections, and follows Ledgerwise’s business rules. I would work to reduce unnecessary handoffs while still escalating when information is missing or a response cannot be verified. Human approval would remain required before sending.

### 3. Compare models and explore agentic research

I would compare Gemini, Groq, and other models on the same research tasks and prospect conversations. I would vary prompts and validation approaches and measure factual accuracy, qualification quality, response reliability, latency, and cost. I would also test an agentic research workflow that can plan searches, identify missing evidence, retry failed steps, and consult alternative sources. I would compare it with the current pipeline before deciding whether the gains justify the cost and complexity.

### 4. Improve contact discovery and the reviewer experience

I would make business contact searches more reliable, especially when looking for finance decision-makers, and improve email verification. I would refine the dashboard and employee workflows based on reviewer feedback. I would also add analytics for research performance, confirmed fits, outreach progress, reply quality, human handoffs, and bookings.

### 5. Complete the safety and reliability work

Before any real-prospect outreach, I would finish permanent opt-out suppression, strengthen security and monitoring, and run more real-world and concurrency tests. I would use those results and reviewer feedback to prioritize further improvements.

My goal would be to make Ledgerwise more reliable and measurable. These are next steps for the prototype.

## 9. Where the project stands

I deployed Ledgerwise to Render, and the repository and documentation are available on GitHub. I completed the public branding pages and Search Console verification, moved Google OAuth publishing to Production, and verified the new refresh token locally. The latest reported local suite passed 100 automated tests. I recorded and uploaded the final Loom demonstration. Full hosted end-to-end verification and two-week availability remain unconfirmed.

Ledgerwise remains a supervised, test-only demonstration. Company discovery still fell short of 30 confirmed fits, AI-generated replies still need careful review, and permanent opt-out suppression is unfinished.

Working with AI sped up implementation and gave me more ways to investigate a problem. My most useful checks were the ones I could explain myself: reading a source to see what it proved, replaying the same saved message after a fix, and verifying what actually landed in Gmail or Cal.com. Those checks helped me decide when a model was useful, when a fixed policy was simpler, and when a late change was too risky for the demo.
