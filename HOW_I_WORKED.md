# How I worked on Ledgerwise

I built a supervised outreach tool for Ledgerwise, a fictional US bookkeeping firm serving e-commerce brands. I worked from Unscale’s brief and planned a complete demonstration from company research and qualification through reviewed outreach, replies, and booked calls. The deliverables included a public repository, a hosted app, the unedited AI sessions, this work log, and a short Loom walkthrough. I tested with real company research and controlled inboxes. I did not reach 30 human-confirmed fits, and permanent opt-out suppression is unfinished.

## 1. Tools I used and why

| Step | What I used and why |
|---|---|
| Research | I used Tavily to find pages, HTTPX to fetch them, and Beautiful Soup to read their text. Tavily Extract helped when ordinary fetching failed. Gemini 3.1 Flash-Lite extracted structured findings, and Python checked whether each quote actually appeared in the source. I added Groq GPT-OSS 120B as an optional research fallback for Gemini service errors or read timeouts. |
| Qualification and contacts | I used Python to score the saved evidence and kept the AI score separate from my fit decision. I dropped in-house email extraction after Hello Cake's examples were saved as contacts. I considered Hunter and explored Apollo for discovery. I kept Tavily for research and used Tomba for manual contact lookup after review. I chose the primary contact myself. A sourced address is different from a delivery-verified address. |
| Email writing and replies | Gemini wrote personalized first emails and ordinary replies. The app checked prospect claims against saved evidence and Ledgerwise claims against approved facts. Gmail API and OAuth let me send approved drafts, import replies manually, and keep each conversation in its own Gmail thread. I replaced Gemini's standalone pricing answer with approved wording after live verification repeatedly contradicted itself. |
| Booking | I chose Cal.com for real availability and booking, with Google Calendar connected and Cal Video for the call. I moved away from direct Google Calendar integration because a test-mode OAuth credential could expire during the required two-week demo period. |
| Platform and hosting | I used FastAPI, Supabase Postgres and Auth, and plain HTML, CSS, and JavaScript. This kept the app within a stack I could explain. I chose Render’s free web service for the hosted demonstration and kept the existing Supabase project. |
| AI tools | I used ChatGPT to brainstorm, understand technical concepts, compare approaches, analyze failures, turn informal ideas into clear Codex instructions, and plan testing, deployment, and documentation. I consulted Claude for planning and Git decisions. Codex inspected the codebase in VS Code, implemented features, investigated errors, proposed fixes, and ran tests. I benchmarked local Ollama models and kept Gemini as the research primary because it did better on supported fit findings. |

## 2. My plan and how it changed

I started with a three-company research script and a JSON file so I could inspect the sources before connecting a database. I looked for US e-commerce product companies in the target revenue and headcount ranges, with active hiring or recent funding or expansion as reasons to reach out. Gemini extracted findings into structured JSON. I used Python to check the quotes and score the evidence. Later I added dashboard batches of up to ten, with automatic discovery or manually supplied domains. Revenue and headcount often remained unknown, so I kept incomplete product businesses visible for later review and separated human fit decisions from AI scores. Demo fit let me test outreach without counting those companies toward the target.

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

### GitHub, Render, and Google OAuth

I prepared the GitHub repository in stages and connected its main branch to Render. I deployed Ledgerwise at https://ledgerwise-ai-sdr.onrender.com and configured the service’s environment variables for the existing Supabase project and integrations. I kept credentials out of Git and retained the reviewer sign-in and server-side dashboard checks. The code and branding changes were pushed to GitHub. The complete hosted workflow still needs a final pass.

Gmail initially used Google OAuth in External / Testing mode. I learned that its refresh token could expire after seven days, halfway through Unscale’s two-week review period even if issued on submission day. I chose to move the OAuth app to Production rather than plan on repeatedly reauthorizing it.

The branding setup needed a public homepage and privacy policy. Codex added About, Privacy, and Terms pages, linked them from login, and kept the dashboard protected. Search Console was checking the root URL, but the verification tag only appeared on About. We added the tag to the signed-out login page reached from the root redirect. I verified ownership in Search Console, configured the branding URLs and authorized domain, and moved the Google OAuth publishing status to In Production. Publishing status is separate from Google’s scope review.

I reauthorized the dedicated Gmail account in OAuth Playground using my own OAuth client and requested both Gmail scopes. Codex tested the new refresh token locally without sending email or syncing the inbox. Refresh succeeded, the Gmail profile returned HTTP 200, the mailbox matched the configured sender, and both gmail.readonly and gmail.send were granted. Newly issued Production tokens are outside the Testing-mode seven-day rule, but can still become invalid. The local check did not prove that the running Render instance loaded or could use that token.

## 5. How I checked the agent

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

## 6. What it costs to run

- **Actual spend.** I developed within free-tier constraints. The records do not establish a billing total. **TODO:** Reconcile provider billing and usage before reporting a figure.
- **Free-tier usage.** I used Tavily, Gemini, and Tomba for research or contacts and tested Groq. Supabase, Gmail, and Cal.com supported the controlled prototype. **TODO:** Verify usage and plan allowances for every service, including the chosen host.
- **Provider quotas recorded during testing.** Gemini allowed 15 requests per minute, 250,000 tokens per minute, and 500 requests per day. My Groq account showed 30 requests per minute, 1,000 requests per day, 8,000 tokens per minute, and 200,000 tokens per day. These are historical figures from development. **TODO:** Recheck current quotas before relying on them for a larger workload.
- **Application limits.** I capped each research batch at 40 Tavily searches, 60 distinct URLs, and 20 model attempts across both model providers. Tomba has a separate app cap of 23 manual lookups, based on my remaining credits. These caps are separate from provider quotas and measured usage.
- **Future operating costs.** I do not have enough data for a monthly estimate. **TODO:** Measure searches and model calls per confirmed fit, lookups per usable contact, and reply volume. Use those figures and the expected hosting and database usage to estimate the monthly cost.

## 7. What I would do next with more time

**1. Compare AI models, tools, and prompting strategies.** I would test Gemini, Groq, and other models using the same research tasks and prospect conversations. I would experiment with different prompts and evaluate factual accuracy, qualification quality, response reliability, latency, and cost. This would help me determine which tools and models are most suitable for each part of the workflow.

**2. Explore agentic frameworks.** I would experiment with an agent-based research workflow that could plan its searches, identify missing information, retry failed steps, and explore alternative sources. I would compare its results against my existing pipeline to determine whether it improves company discovery and qualification without introducing unnecessary complexity or costs.

**3. Improve the platform and user experience.** I would refine the interface to make it more polished, intuitive, and efficient for sales employees. I would also add analytics dashboards to monitor research performance, qualified companies, outreach progress, AI response quality, human interventions, and meeting bookings. I would improve contact discovery and email verification as well.

**4. Complete limitations and improve reliability.** I would implement permanent opt-out suppression before enabling real-prospect outreach, improve company qualification to reach the 30 confirmed matches, strengthen security and monitoring, and conduct more extensive real-world and concurrency testing. I would use the results and user feedback to prioritize further improvements.

My overall goal would be to evolve Ledgerwise from a supervised prototype into a more reliable, measurable, and production-ready AI SDR platform.

## 8. Where the project stands

I committed and pushed the Ledgerwise application to GitHub and deployed it to Render. I completed the public branding pages and Search Console verification, moved Google OAuth publishing to Production, and verified the new refresh token locally. The latest reported local suite passed 100 automated tests. My remaining demo tasks are to test the complete hosted workflow, verify that evaluators can sign in, and record the Loom walkthrough. Two-week availability is still to be confirmed. The 30-fit target and permanent opt-out suppression remain unfinished, and the project stays in test mode.

Working with AI sped up implementation and gave me more ways to investigate a problem. My most useful checks were the ones I could explain myself: reading a source to see what it proved, replaying the same saved message after a fix, and verifying what actually landed in Gmail or Cal.com. Those checks helped me decide when a model was useful, when a fixed policy was simpler, and when a late change was too risky for the demo.
