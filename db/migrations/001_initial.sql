-- 001_initial.sql — initial schema for Distribuidor de Abundância.
--
-- Conventions:
--   * Status/category columns are text + CHECK instead of ENUM, so they are easy to evolve.
--   * Locations are geography(Point, 4326) (WGS 84, distances in meters).
--   * PII (name, phone, address, exact location) lives ONLY in person_pii. Every other table is
--     PII-free and safe to aggregate, mask and send (masked) to the LLM.
--
-- What the database enforces vs. what the application must do:
--   * Encryption is NOT done by the database. person_pii only has bytea columns to hold
--     ciphertext; the app must encrypt before writing (not implemented yet).
--   * Anjo approval IS enforced, partially: CHECKs reject an approved/rejected need or a
--     confirmed delivery without a recorded anjo + timestamp, and a trigger rejects matches
--     for needs that are not 'aprovada'. The database does NOT check
--     that the anjo is active or belongs to the same neighborhood (app rule for now).

begin;

create extension if not exists postgis;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- PII (LGPD)
-- ---------------------------------------------------------------------------
-- The *_enc columns must hold ciphertext produced by the application (planned: key from an env
-- var, never sent to the database or in a query string, so it cannot leak through DB logs).
-- The database cannot tell ciphertext from plaintext; it only stores bytes.
-- Only the app's PII layer reads this table. Its contents are NEVER sent to the LLM, and
-- donors never see a recipient's name or address.
create table person_pii (
    id                 uuid primary key default gen_random_uuid(),
    name_enc           bytea,
    phone_enc          bytea,
    phone_hash         bytea unique           -- HMAC-SHA256(phone): look up without decrypting
                       check (octet_length(phone_hash) = 32),
    address_enc        bytea,
    exact_location_enc bytea,                 -- exact lat/long as shared on WhatsApp
    created_at         timestamptz not null default now(),
    deleted_at         timestamptz            -- LGPD erasure: wipe *_enc columns and set this
);

-- ---------------------------------------------------------------------------
-- Reference data and people (no PII)
-- ---------------------------------------------------------------------------
create table neighborhoods (
    id         bigint generated always as identity primary key,
    name       text not null,
    city       text not null,
    centroid   geography(Point, 4326),
    created_at timestamptz not null default now(),
    unique (name, city)
);

-- Anyone who talks to the bot: donor, person in need, referrer, referred family, anjo.
create table participants (
    id              uuid primary key default gen_random_uuid(),
    pii_id          uuid unique references person_pii (id) on delete set null,
    neighborhood_id bigint references neighborhoods (id),
    consent_at      timestamptz,              -- accepted terms / data use (LGPD)
    opted_out_at    timestamptz,
    created_at      timestamptz not null default now()
);

-- Neighborhood volunteers who validate requests and confirm deliveries.
create table anjos (
    id              uuid primary key default gen_random_uuid(),
    participant_id  uuid not null unique references participants (id),
    neighborhood_id bigint not null references neighborhoods (id),
    active          boolean not null default true,
    approved_at     timestamptz,
    created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Conversation state machine and webhook dedup
-- ---------------------------------------------------------------------------
create table conversations (
    participant_id uuid primary key references participants (id) on delete cascade,
    porta          text check (porta in ('porta_ajudar', 'porta_preciso', 'porta_indico')),
    step           text not null default 'menu',
    -- Fields collected so far, PII-free only. The first name asked in "Preciso de ajuda"
    -- (and any name, phone or address) goes straight to person_pii, never into this jsonb.
    data           jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- Meta retries webhooks; a message is processed only if its wamid inserts cleanly.
create table inbound_messages (
    wamid       text primary key,
    received_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Door 1 — "Quero ajudar": donations
-- ---------------------------------------------------------------------------
-- approx_location: the app rounds lat/long to 3 decimal places (~100 m) before storing.
-- Good enough for distance ranking, not enough to locate a house.
create table donations (
    id              uuid primary key default gen_random_uuid(),
    donor_id        uuid not null references participants (id),
    category        text not null check (category in
                        ('cesta_basica', 'alimento', 'comida_pronta', 'laticinio', 'doce', 'outro')),
    description     text,
    quantity        numeric not null check (quantity > 0),
    unit            text not null,            -- 'cesta', 'kg', 'marmita', 'unidade'...
    expires_at      timestamptz,
    approx_location geography(Point, 4326),
    neighborhood_id bigint references neighborhoods (id),
    fulfillment     text not null check (fulfillment in ('retirada', 'entrega', 'ambos')),
    status          text not null default 'disponivel' check (status in
                        ('disponivel', 'reservada', 'entregue', 'vencida', 'cancelada')),
    created_at      timestamptz not null default now(),
    check (approx_location is not null or neighborhood_id is not null)
);

-- ---------------------------------------------------------------------------
-- Door 2 — "Preciso de ajuda": needs (validated by an anjo before matching)
-- ---------------------------------------------------------------------------
create table needs (
    id              uuid primary key default gen_random_uuid(),
    beneficiary_id  uuid not null references participants (id),
    household_size  integer not null check (household_size > 0),
    children_count  integer not null default 0 check (children_count >= 0),
    urgency         text not null check (urgency in ('hoje', 'esta_semana', 'pode_esperar')),
    -- What the family needs; same vocabulary as donations.category, at least one.
    categories      text[] not null check (
                        cardinality(categories) > 0
                        and categories <@ array['cesta_basica', 'alimento', 'comida_pronta',
                                                'laticinio', 'doce', 'outro']::text[]),
    approx_location geography(Point, 4326),   -- rounded like donations.approx_location
    neighborhood_id bigint references neighborhoods (id),
    status          text not null default 'aguardando_validacao' check (status in
                        ('aguardando_validacao', 'aprovada', 'rejeitada', 'atendida', 'encerrada')),
    validated_by    uuid references anjos (id),
    validated_at    timestamptz,
    created_at      timestamptz not null default now(),
    check (children_count <= household_size),
    check (approx_location is not null or neighborhood_id is not null),
    -- Human in the loop: no request leaves validation without a named anjo.
    check (status not in ('aprovada', 'rejeitada', 'atendida')
           or (validated_by is not null and validated_at is not null))
);

-- ---------------------------------------------------------------------------
-- Door 3 — "Sei de alguém": referrals
-- ---------------------------------------------------------------------------
-- The referred family is its own participant (needs.beneficiary_id), with its own PII row;
-- its phone may be null if it has no WhatsApp. A referral without the family's consent
-- cannot be stored.
create table referrals (
    id                   uuid primary key default gen_random_uuid(),
    referrer_id          uuid not null references participants (id),
    need_id              uuid not null unique references needs (id),
    family_consent       boolean not null check (family_consent),
    consent_confirmed_at timestamptz not null default now(),
    created_at           timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Matching and delivery
-- ---------------------------------------------------------------------------
-- Matches are produced by deterministic SQL, never by the LLM. `reasons` records why
-- (distance, urgency, children, expiry) so every match is explainable.
-- One donation can be split across families: each match takes a `quantity` of it
-- (same unit as donations.unit).
create table matches (
    id          uuid primary key default gen_random_uuid(),
    donation_id uuid not null references donations (id),
    need_id     uuid not null references needs (id),
    quantity    numeric not null check (quantity > 0),
    distance_m  numeric not null check (distance_m >= 0),
    score       numeric not null,
    reasons     jsonb not null,
    status      text not null default 'proposto' check (status in
                    ('proposto', 'aceito', 'recusado', 'cancelado', 'concluido')),
    created_at  timestamptz not null default now(),
    unique (donation_id, need_id)
);

-- Human in the loop: only needs approved by an anjo can be matched.
-- Fires on insert and when need_id changes. Not on every update: once delivered, the need moves
-- to 'atendida' and the match must still be able to move to 'concluido'.
-- FOR SHARE locks the need row so its status can't change mid-transaction.
create function matches_require_approved_need() returns trigger
language plpgsql as $$
declare
    need_status text;
begin
    select status into need_status from needs where id = new.need_id for share;
    if need_status is distinct from 'aprovada' then
        raise exception 'need % has status %, only ''aprovada'' needs can be matched',
            new.need_id, coalesce(need_status, '<missing>')
            using errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create trigger matches_require_approved_need
    before insert or update of need_id on matches
    for each row execute function matches_require_approved_need();

create table deliveries (
    id            uuid primary key default gen_random_uuid(),
    match_id      uuid not null unique references matches (id),
    method        text not null check (method in ('retirada', 'entrega')),
    scheduled_for timestamptz,
    status        text not null default 'agendada' check (status in
                      ('agendada', 'confirmada', 'falhou')),
    confirmed_by  uuid references anjos (id),
    confirmed_at  timestamptz,
    notes         text,                       -- operational notes only; no PII
    created_at    timestamptz not null default now(),
    -- Human in the loop: an anjo confirms every delivery.
    check (status <> 'confirmada' or (confirmed_by is not null and confirmed_at is not null))
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
create index neighborhoods_centroid_gix   on neighborhoods using gist (centroid);
create index donations_location_gix       on donations using gist (approx_location);
create index needs_location_gix           on needs using gist (approx_location);

create index donations_available_expiry_idx on donations (expires_at) where status = 'disponivel';
create index needs_approved_urgency_idx     on needs (urgency) where status = 'aprovada';

create index participants_neighborhood_idx on participants (neighborhood_id);
create index anjos_neighborhood_idx        on anjos (neighborhood_id);
create index donations_donor_idx           on donations (donor_id);
create index donations_neighborhood_idx    on donations (neighborhood_id);
create index needs_beneficiary_idx         on needs (beneficiary_id);
create index needs_neighborhood_idx        on needs (neighborhood_id);
create index needs_validated_by_idx        on needs (validated_by);
create index referrals_referrer_idx        on referrals (referrer_id);
create index matches_need_idx              on matches (need_id);
create index deliveries_confirmed_by_idx   on deliveries (confirmed_by);

commit;
