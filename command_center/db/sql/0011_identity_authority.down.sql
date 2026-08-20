-- Restore 0010's credential issue: the tier rule declines to have an opinion
-- about a caller with no principal, and there is no mode to consult.
--
-- Downgrading here re-opens the fail-open this migration closed. Stated plainly
-- because it is what downgrading means: a database on 0010 skips the
-- strictly-lower-trust check for any caller that has no `principal` row, which
-- on an unbootstrapped database is every caller. That is the state 0010
-- shipped, and returning to it is the only honest downgrade — a third posture
-- in which the check half-applies is one nobody designed or tested.
--
-- The `principal` rows written by `identity-bootstrap` are NOT removed. They
-- are data, not schema: they carry the `enrolled_by` chain and every
-- `principal_event` that references them, and a downgrade that deleted them
-- would take the audit of its own rollout with it. They are also harmless on
-- 0010 — an extra principal makes `current_principal()` resolve, which is the
-- direction that adds enforcement rather than removing it.

CREATE OR REPLACE FUNCTION identity_issue_db_credential(
    p_principal_id   text,
    p_secret_hash    text,
    p_scram_verifier text,
    p_ttl            interval
) RETURNS TABLE (issued_credential_id text, issue_refuse_reason text)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    p             principal%ROWTYPE;
    v_id          text;
    v_ttl         interval;
    v_caller      text;
    v_caller_tier integer;
    r             record;
BEGIN
    SELECT * INTO p FROM principal WHERE principal_id = p_principal_id FOR UPDATE;
    IF NOT FOUND OR p.state <> 'active' THEN
        PERFORM _principal_audit(p_principal_id, 'issue', 'rejected', NULL,
                                 'principal_inactive', NULL);
        RETURN QUERY SELECT NULL::text, 'principal_inactive'::text;
        RETURN;
    END IF;

    v_caller := current_principal();
    IF v_caller IS NOT NULL AND v_caller IS DISTINCT FROM p_principal_id THEN
        SELECT trust_tier INTO v_caller_tier FROM principal WHERE principal_id = v_caller;
        IF v_caller_tier >= p.trust_tier THEN
            PERFORM _principal_audit(p_principal_id, 'issue', 'rejected', NULL,
                     'tier_violation',
                     jsonb_build_object('caller', v_caller, 'caller_tier', v_caller_tier,
                                        'target_tier', p.trust_tier));
            RETURN QUERY SELECT NULL::text, 'tier_violation'::text;
            RETURN;
        END IF;
    END IF;

    v_ttl := least(coalesce(p_ttl, interval '15 minutes'), interval '1 hour');

    FOR r IN SELECT credential_id FROM principal_credential
              WHERE principal_id = p_principal_id AND revoked_at IS NULL LOOP
        UPDATE principal_credential
           SET revoked_at = now(), revoke_reason = 'rotated', updated_at = now()
         WHERE credential_id = r.credential_id;
        PERFORM _principal_audit(p_principal_id, 'revoke', 'granted',
                                 r.credential_id, 'rotated', NULL);
    END LOOP;

    v_id := _identity_new_id('cred_');
    INSERT INTO principal_credential (
        credential_id, principal_id, secret_hash,
        issued_at, expires_at, issued_from_addr, created_at, updated_at)
    VALUES (v_id, p_principal_id, p_secret_hash,
            now(), now() + v_ttl, inet_client_addr(), now(), now());

    IF p.kind = 'worker_host' THEN
        PERFORM identity_enable_role(p.db_role);
    END IF;
    PERFORM identity_set_role_secret(p.db_role, p_scram_verifier, now() + v_ttl);

    PERFORM _principal_audit(p_principal_id, 'issue', 'granted', v_id, NULL,
                             jsonb_build_object('ttl', v_ttl::text));
    RETURN QUERY SELECT v_id, NULL::text;
END
$$;

DROP FUNCTION IF EXISTS identity_set_enforcement(text);
DROP FUNCTION IF EXISTS identity_enforcement();
DROP TABLE IF EXISTS identity_policy;
