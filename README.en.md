# Distribuidor de Abundância

[🇧🇷 Português](README.md) · 🇺🇸 English

*"Abundance Distributor"*: a WhatsApp agent that connects people who have food to spare with
people who need it, in Várzea Paulista and Jundiaí (São Paulo, Brazil). It is a non-profit
social project.

## How it works

Whoever messages the number picks one of three "doors" (the bot speaks Brazilian Portuguese):

| Door | Who | What we ask |
|---|---|---|
| **Quero ajudar** ("I want to help") | Donor | What, how much, expiry, neighborhood, pickup or delivery |
| **Preciso de ajuda** ("I need help") | Person or family | Neighborhood, household size, children, urgency, first name |
| **Sei de alguém** ("I know someone") | Referrer | Same family details, plus confirmation that **the family agreed** |

**Anjos** ("angels") are neighborhood volunteers. They validate every new request before it
enters matching, and they confirm every delivery.

## Architecture principles

- **The LLM handles language; code handles rules.** The conversation flow is a state machine
  in code, and the LLM only turns free text into structured fields.
  See [ADR 0001](docs/adr/0001-llm-para-linguagem-sql-para-regras.md) (in Portuguese).
- **Deterministic, explainable matching.** Who gets what is decided by SQL with PostGIS
  (distance, urgency, children, expiry), never by the LLM. Every match stores its reasons.
- **Human in the loop.** Anjos approve requests and confirm deliveries. The database rejects an
  approved request or a confirmed delivery with no anjo recorded (see [Status](#status)).
- **Privacy (LGPD, Brazil's data protection law).** Names, phone numbers, addresses and exact
  locations live in a separate table, designed to hold data encrypted by the app. The LLM
  only receives masked data ("family of 4, neighborhood X"). The donation and request tables
  keep only an approximate location (~100 m). Donors never see a recipient's name or address.
- **Buttons and native location over free text.** This means fewer LLM calls and fewer errors.
- **Short flows.** WhatsApp gives 1,000 free service messages per number per month,
  then charges.

## Stack

- Python 3.12 + FastAPI (WhatsApp Cloud API webhook)
- Postgres + PostGIS (Supabase for the MVP)
- Deploy: Azure Container Apps (scale to zero)
- Observability: Langfuse traces + an eval set of fixed conversations
- Analytics: medallion pipeline (bronze/silver/gold) feeding a public impact dashboard

## Layout

```
app/main.py       FastAPI webhook: signature, dedup, background dispatch, sending
app/flow.py       conversation state machine (pure functions, no I/O)
app/repository.py database access: dedup, participants, conversation state
app/pii.py        phone hash (HMAC); encryption still to come
app/config.py     settings from environment variables (pydantic-settings)
app/db.py         connection pool (psycopg)
app/loop.py       selector event loop factory for Windows dev
db/migrations/    SQL schema (Postgres + PostGIS)
db/tests/         SQL schema tests
tests/            pytest suite for the app
docs/adr/         architecture decision records
```

## Running locally

Prerequisites: Python 3.12, a Postgres with PostGIS (Docker or Supabase), and a Meta app with
the WhatsApp product.

**bash** (Linux, macOS, Git Bash)

```bash
# 1. Environment and dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Environment variables
cp .env.example .env             # fill in the WA_* values and generate PII_HMAC_KEY

# 3. Database (host port 5433, so it doesn't clash with an installed Postgres)
docker run -d --name abundancia-db -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=abundancia -p 5433:5432 postgis/postgis:16-3.4
# wait for the image to finish its init (about 30 s the first time) before applying the schema
psql "postgresql://postgres:postgres@localhost:5433/abundancia" \
  -f db/migrations/001_initial.sql

# 4. Server
uvicorn app.main:app --reload --port 8000

# 5. Public HTTPS so Meta can reach the webhook
cloudflared tunnel --url http://localhost:8000   # or: ngrok http 8000
```

**PowerShell** (Windows)

```powershell
# 1. Environment and dependencies
python -m venv .venv
.venv\Scripts\Activate.ps1       # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"

# 2. Environment variables
Copy-Item .env.example .env      # fill in the WA_* values and generate PII_HMAC_KEY

# 3. Database (host port 5433, so it doesn't clash with an installed Postgres)
docker run -d --name abundancia-db -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=abundancia -p 5433:5432 postgis/postgis:16-3.4
# wait for the image to finish its init (about 30 s the first time) before applying the schema
Get-Content db\migrations\001_initial.sql | docker exec -i abundancia-db `
  psql -U postgres -d abundancia -v ON_ERROR_STOP=1

# 4. Server
uvicorn app.main:app --reload --port 8000
# without --reload on Windows, add: --loop app.loop:selector_loop  (see app/loop.py)

# 5. Public HTTPS so Meta can reach the webhook
cloudflared tunnel --url http://localhost:8000   # or: ngrok http 8000
```

In PowerShell the migration runs through the `psql` inside the container, so you don't need
the Postgres client installed on Windows. The app reads `.env` by itself (pydantic-settings).
On Windows, async psycopg does not run on the default (Proactor) event loop; with `--reload`
uvicorn already picks the right loop, and without `--reload` pass
`--loop app.loop:selector_loop`.

Mind the `postgis/postgis` image's init: on first boot it starts Postgres on the internal
socket only, runs the PostGIS scripts and restarts. If the migration runs in that window, the
PostGIS script fails and the container dies. Wait until port 5433 accepts connections from
the host.

### App tests

```bash
pytest
```

The tests for `app/flow.py`, `app/pii.py` and the webhook run without a database. The ones for
`app/repository.py` use `DATABASE_URL` (default: the Docker instance above) inside a
rolled-back transaction, and are skipped when the database is unreachable or has no schema.

### Schema tests

[`db/tests/001_schema_test.sql`](db/tests/001_schema_test.sql) checks the rules the database
enforces: valid categories, anjo approval, family consent, the `matches` trigger and more. It
runs inside a transaction that ends in `ROLLBACK`, so it leaves no data behind. On the first
broken rule, psql stops with a non-zero exit code. Run it after applying the migration:

```bash
psql "postgresql://postgres:postgres@localhost:5433/abundancia" \
  -v ON_ERROR_STOP=1 -f db/tests/001_schema_test.sql
```

```powershell
Get-Content db\tests\001_schema_test.sql | docker exec -i abundancia-db `
  psql -U postgres -d abundancia -v ON_ERROR_STOP=1
```

Each rule prints a `PASS` line, ending with `All schema tests passed.`

### Meta webhook

Finally, in the Meta dashboard (WhatsApp > Configuration), register
`https://<your-tunnel>/webhook` with the same `WA_VERIFY_TOKEN` and subscribe to the
`messages` field.

## Status

Early MVP.

**Done**
- Webhook: validates Meta's signature, returns 200 right away and processes each message in
  the background.
- Postgres persistence: dedup by `wamid` in `inbound_messages`, participant looked up by the
  phone's HMAC (the number itself is not stored) and conversation state in `conversations`,
  all in one transaction per message.
- State machine ([`app/flow.py`](app/flow.py)): three-door menu, first question of each door,
  native location rounded to ~100 m and the word *menu* to start over. Free text mid-flow is
  acknowledged but not interpreted yet.
- Database schema ([`001_initial.sql`](db/migrations/001_initial.sql)). The database already
  enforces:
  - an approved, rejected or fulfilled request must have an anjo and a validation timestamp;
  - only approved requests can be matched (trigger on `matches`);
  - a confirmed delivery must have an anjo and a confirmation timestamp;
  - a referral is stored only with the family's consent;
  - the number of children can't exceed the household size.

**Planned**
- **Full flow for each door:** today the conversation stops at the first question. The next
  steps are missing, with buttons wherever possible and LLM field extraction where free text
  is unavoidable.
- **PII encryption:** the `person_pii` table only has `bytea` columns to hold ciphertext. The
  app will do the encryption, with a key from an environment variable, and that is not
  implemented yet. The database encrypts nothing on its own. Only `phone_hash` is written for
  now. Open question: after LGPD erasure (`deleted_at`), the unique `phone_hash` still blocks
  the same phone from registering again.
- **Anjo rules:** the database doesn't yet check that the anjo is active or belongs to the
  request's neighborhood.
- SQL matching, the anjo flow on WhatsApp, Langfuse, deployment.

Architecture decisions live in [`docs/adr/`](docs/adr/).

## License

[MIT](LICENSE) © David Nascimento da Silva
