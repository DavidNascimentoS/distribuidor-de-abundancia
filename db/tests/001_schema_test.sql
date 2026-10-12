-- 001_schema_test.sql — checks the constraints and triggers of 001_initial.sql.
--
-- Run against a database where the migration was applied, with ON_ERROR_STOP so the first
-- failing check aborts with a non-zero exit code:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/001_schema_test.sql
--
-- Everything runs inside one transaction that is rolled back: no data is left behind.

\set VERBOSITY terse
begin;

-- Runs `stmt` and fails the test if it does NOT raise an error.
create procedure pg_temp.expect_error(label text, stmt text)
language plpgsql as $$
begin
    begin
        execute stmt;
    exception when others then
        raise notice 'PASS  %  (rejected: %)', label, sqlerrm;
        return;
    end;
    raise exception 'FAIL  %  (statement was accepted, expected an error)', label;
end;
$$;

-- Runs `stmt` and fails the test if it raises an error.
create procedure pg_temp.expect_ok(label text, stmt text)
language plpgsql as $$
begin
    execute stmt;
    raise notice 'PASS  %', label;
exception when others then
    raise exception 'FAIL  %  (unexpected error: %)', label, sqlerrm;
end;
$$;

-- ---------------------------------------------------------------------------
-- Fixtures
-- ---------------------------------------------------------------------------
insert into neighborhoods (id, name, city, centroid) overriding system value
    values (900001, 'Teste', 'Várzea Paulista', 'SRID=4326;POINT(-46.828 -23.211)');

insert into participants (id, neighborhood_id) values
    ('00000000-0000-0000-0000-000000000001', 900001),   -- donor
    ('00000000-0000-0000-0000-000000000002', 900001),   -- family
    ('00000000-0000-0000-0000-000000000003', 900001);   -- anjo

insert into anjos (id, participant_id, neighborhood_id) values
    ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-000000000003', 900001);

insert into donations (id, donor_id, category, quantity, unit, neighborhood_id, fulfillment)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-000000000001',
            'cesta_basica', 3, 'cesta', 900001, 'ambos');

-- ---------------------------------------------------------------------------
-- needs.categories
-- ---------------------------------------------------------------------------
call pg_temp.expect_error('needs: empty categories', $q$
    insert into needs (beneficiary_id, household_size, urgency, neighborhood_id, categories)
    values ('00000000-0000-0000-0000-000000000002', 4, 'hoje', 900001, '{}') $q$);

call pg_temp.expect_error('needs: invalid category', $q$
    insert into needs (beneficiary_id, household_size, urgency, neighborhood_id, categories)
    values ('00000000-0000-0000-0000-000000000002', 4, 'hoje', 900001, '{cesta_basica,fralda}') $q$);

call pg_temp.expect_error('needs: missing categories', $q$
    insert into needs (beneficiary_id, household_size, urgency, neighborhood_id)
    values ('00000000-0000-0000-0000-000000000002', 4, 'hoje', 900001) $q$);

call pg_temp.expect_ok('needs: valid categories (awaiting validation)', $q$
    insert into needs (id, beneficiary_id, household_size, children_count, urgency,
                       neighborhood_id, categories)
    values ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-000000000002',
            4, 2, 'hoje', 900001, '{cesta_basica,laticinio}') $q$);

-- ---------------------------------------------------------------------------
-- Anjo validation on needs
-- ---------------------------------------------------------------------------
call pg_temp.expect_error('needs: more children than household', $q$
    insert into needs (beneficiary_id, household_size, children_count, urgency,
                       neighborhood_id, categories)
    values ('00000000-0000-0000-0000-000000000002', 2, 3, 'hoje', 900001, '{alimento}') $q$);

call pg_temp.expect_error('needs: approved without anjo', $q$
    insert into needs (beneficiary_id, household_size, urgency, neighborhood_id, categories, status)
    values ('00000000-0000-0000-0000-000000000002', 2, 'hoje', 900001, '{alimento}', 'aprovada') $q$);

call pg_temp.expect_ok('needs: approved with anjo', $q$
    insert into needs (id, beneficiary_id, household_size, urgency, neighborhood_id, categories,
                       status, validated_by, validated_at)
    values ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-000000000002',
            2, 'esta_semana', 900001, '{alimento}',
            'aprovada', '00000000-0000-0000-0000-0000000000a1', now()) $q$);

-- ---------------------------------------------------------------------------
-- Referrals
-- ---------------------------------------------------------------------------
call pg_temp.expect_error('referrals: without family consent', $q$
    insert into referrals (referrer_id, need_id, family_consent)
    values ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000e1', false) $q$);

call pg_temp.expect_ok('referrals: with family consent', $q$
    insert into referrals (referrer_id, need_id, family_consent)
    values ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000e1', true) $q$);

-- ---------------------------------------------------------------------------
-- matches: approved-need trigger and quantity
-- ---------------------------------------------------------------------------
call pg_temp.expect_error('matches: need awaiting validation (trigger)', $q$
    insert into matches (donation_id, need_id, quantity, distance_m, score, reasons)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000e1',
            1, 0, 1, '{}') $q$);

call pg_temp.expect_error('matches: quantity 0', $q$
    insert into matches (donation_id, need_id, quantity, distance_m, score, reasons)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000e2',
            0, 0, 1, '{}') $q$);

call pg_temp.expect_ok('matches: approved need', $q$
    insert into matches (id, donation_id, need_id, quantity, distance_m, score, reasons)
    values ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000d1',
            '00000000-0000-0000-0000-0000000000e2', 1, 0, 1, '{"distance": "same neighborhood"}') $q$);

call pg_temp.expect_error('matches: repoint to non-approved need (trigger)', $q$
    update matches set need_id = '00000000-0000-0000-0000-0000000000e1'
    where id = '00000000-0000-0000-0000-0000000000c1' $q$);

update needs set status = 'atendida' where id = '00000000-0000-0000-0000-0000000000e2';
call pg_temp.expect_ok('matches: need atendida, match can still be concluido', $q$
    update matches set status = 'concluido'
    where id = '00000000-0000-0000-0000-0000000000c1' $q$);

update needs set status = 'aprovada',
                 validated_by = '00000000-0000-0000-0000-0000000000a1',
                 validated_at = now()
where id = '00000000-0000-0000-0000-0000000000e1';
call pg_temp.expect_ok('matches: split same donation across a second approved need', $q$
    insert into matches (donation_id, need_id, quantity, distance_m, score, reasons)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000e1',
            2, 0, 1, '{}') $q$);

-- ---------------------------------------------------------------------------
-- deliveries
-- ---------------------------------------------------------------------------
call pg_temp.expect_error('deliveries: confirmed without anjo', $q$
    insert into deliveries (match_id, method, status)
    values ('00000000-0000-0000-0000-0000000000c1', 'retirada', 'confirmada') $q$);

call pg_temp.expect_ok('deliveries: confirmed with anjo', $q$
    insert into deliveries (match_id, method, status, confirmed_by, confirmed_at)
    values ('00000000-0000-0000-0000-0000000000c1', 'retirada', 'confirmada',
            '00000000-0000-0000-0000-0000000000a1', now()) $q$);

-- ---------------------------------------------------------------------------
-- person_pii
-- ---------------------------------------------------------------------------
call pg_temp.expect_error('person_pii: phone_hash not 32 bytes', $q$
    insert into person_pii (phone_hash) values ('\x00ff'::bytea) $q$);

\echo 'All schema tests passed.'
rollback;
