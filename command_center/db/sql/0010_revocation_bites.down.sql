-- Restore 0003's revocation: login and sessions only, privileges left in place.
--
-- The privilege withdrawal is the whole of this migration, so undoing it means
-- putting the memberships back. Every per-host role named by a `worker_host`
-- principal is re-granted `aicc_worker`, INCLUDING roles 0010 stripped during
-- an incident — stated plainly because it is what downgrading means here: 0003's
-- posture is that a disabled role keeps its grants and is held off only by
-- `NOLOGIN` and by having been terminated, and a downgrade that returned to
-- some third state nobody designed would be worse than returning to the one
-- that was shipped. Roles are cluster objects, so this reaches hosts enrolled
-- against other databases in the cluster only if they have a row here; that is
-- the same scope the enrolment protocol has always had.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT db_role FROM principal WHERE kind = 'worker_host' LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.db_role) THEN
            EXECUTE format('GRANT %I TO %I', 'aicc_worker', r.db_role);
        END IF;
    END LOOP;
END
$$;

CREATE OR REPLACE FUNCTION identity_disable_role(p_role text) RETURNS integer
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_killed integer;
BEGIN
    IF p_role IS NULL THEN RETURN 0; END IF;
    EXECUTE format('ALTER ROLE %I NOLOGIN VALID UNTIL %L', p_role, '-infinity');
    SELECT count(*) INTO v_killed FROM (
        SELECT pg_terminate_backend(pid) FROM pg_stat_activity
         WHERE usename = p_role AND pid <> pg_backend_pid()) t;
    RETURN v_killed;
END
$$;

CREATE OR REPLACE FUNCTION identity_revoke_credential(p_credential_id text, p_reason text)
    RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE c principal_credential%ROWTYPE; p principal%ROWTYPE;
BEGIN
    SELECT * INTO c FROM principal_credential
      WHERE credential_id = p_credential_id FOR UPDATE;
    IF NOT FOUND OR c.revoked_at IS NOT NULL THEN
        RETURN false;
    END IF;

    UPDATE principal_credential
       SET revoked_at = now(), revoke_reason = p_reason,
           revoked_by = current_principal(), updated_at = now()
     WHERE credential_id = p_credential_id;

    SELECT * INTO p FROM principal WHERE principal_id = c.principal_id;
    PERFORM _principal_audit(c.principal_id, 'revoke', 'granted', c.credential_id,
                             p_reason, NULL);
    IF p.kind = 'worker_host' THEN
        PERFORM identity_disable_role(p.db_role);
    END IF;
    RETURN true;
END
$$;

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

    PERFORM identity_set_role_secret(p.db_role, p_scram_verifier, now() + v_ttl);

    PERFORM _principal_audit(p_principal_id, 'issue', 'granted', v_id, NULL,
                             jsonb_build_object('ttl', v_ttl::text));
    RETURN QUERY SELECT v_id, NULL::text;
END
$$;

DROP FUNCTION IF EXISTS identity_enable_role(text);
