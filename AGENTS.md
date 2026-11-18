# Project: Ledgerwise AI SDR platform (case study)

## What we're building
An AI platform that researches US e-commerce brands ($1M-10M revenue, 5-50 employees), qualifies them with sourced reasons, drafts personal first emails, answers replies, and books calls. A non-technical sales team reviews everything in a web dashboard.

## Stack
- Backend: Python, FastAPI
- Frontend: plain HTML, CSS, JavaScript (no frameworks)
- Database: Postgres (Supabase free tier)
- API keys only in environment variables, never in code or git

## Hard rules (client requirements, enforce in code, not only in prompts)
1. Never quote a price in any message.
2. Every fact in an email must come from a stored source (URL + quote). Block the email if any claim has no source. Never invent facts.
3. If a prospect says stop, unsubscribe or not interested: add to a suppression list and never contact them again. Check the list before every send.
4. If asked whether they're talking to a bot, answer honestly.
5. If the AI is unsure, hand the thread to a human.

## How to work with me
- Plan first. Do not write code until I approve the plan.
- Do one small task at a time. Keep changes small.
- Explain every change in simple words, because I must be able to defend all of it.
- After each finished task, tell me what changed and suggest a git commit message.
- If you are unsure, ask me. Never guess.