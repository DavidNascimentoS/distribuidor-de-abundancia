# Distribuidor de Abundância

Social project (non-profit): a WhatsApp agent that connects people who can donate food
with people who need it, in Várzea Paulista/Jundiaí (SP, Brazil). Also a public portfolio
piece. Code, identifiers and commit messages are in English. README.md and ADRs are in
Brazilian Portuguese, with an English README.en.md kept in sync. User-facing messages are
in Brazilian Portuguese.

## Three entry doors
1. "Quero ajudar" — donor registers what, how much, expiry, neighborhood, pickup/delivery.
2. "Preciso de ajuda" — person in need: neighborhood, household size, children, urgency, first name.
3. "Sei de alguém" — someone refers a family; must confirm the family agreed.
Neighborhood volunteers ("anjos") validate new requests and confirm deliveries.

## Architecture principles
- LLM handles language, code handles rules. The conversation flow is a state machine in
  code; the LLM only extracts fields from free text into structured output.
- Matching is deterministic SQL (PostGIS distance, urgency, children, expiry) — never the LLM.
  Every match must be explainable.
- Human in the loop: anjos approve new requests and confirm deliveries.
- Privacy (LGPD): PII (name, phone, address) lives in a separate encrypted table. Never send
  PII to the LLM — pass masked data only ("family of 4, neighborhood X"). Donors never see
  recipient name or address.
- Prefer WhatsApp buttons and native location over free text to cut LLM calls and errors.
- WhatsApp service messages: 1,000 free per number per month, then paid. Keep flows short.

## Stack
- Python 3.12, FastAPI (`main.py` = WhatsApp Cloud API webhook)
- Postgres + PostGIS (Supabase free tier for MVP)
- Deploy: Azure Container Apps (scale to zero)
- Observability: Langfuse traces + eval set of fixed conversations from the script
- Analytics: medallion pipeline (bronze/silver/gold) feeding a public impact dashboard

## Conventions
- Secrets only via environment variables (`.env`, never committed).
- Webhook must return 200 fast; slow work (LLM, DB) runs in background.
- Deduplicate WhatsApp messages by `wamid`.
- Record significant decisions as short ADRs in `docs/adr/`.
