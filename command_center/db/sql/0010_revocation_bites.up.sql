-- Revocation that actually bites (VOYN-W0-AICC-SRV-02b).
--
-- 0003 built the identity gate and `identity_disable_role()`, and asserted the
-- half that is easy to assert: after revoking a host, a NEW connection is
-- refused. This migration is about the half that was assumed rather than
-- measured — what happens to a connection that is ALREADY OPEN.
--
-- THREE MECHANISMS, MEASURED SEPARATELY ON PostgreSQL 17.6. Each one refutes an
-- assumption that is easy to hold and expensive to hold wrongly.
--
--   1. `ALTER ROLE ... NOLOGIN VALID UNTIL '-infinity'` DOES NOT DISTURB AN
--      ESTABLISHED SESSION. PostgreSQL evaluates both attributes at
--      authentication time and never again, so the revoked host's open
--      connection goes on serving SELECTs and UPDATEs indefinitely. What this
--      statement stops is the NEXT connection, and nothing else. 0003 already
--      says this in a comment; nothing tested it, and "revoked" read as
--      "cut off".
--
--   2. `REVOKE` OF A PRIVILEGE *DOES* REACH AN OPEN SESSION — including a
--      privilege held indirectly, through membership of a group. PostgreSQL
--      re-checks the ACL on every statement and invalidates the peer backends'
--      caches at commit, so the very next statement on the connection that was
--      already open is refused. This holds for a cached generic plan of a
--      prepared statement, and inside a transaction the session had already
--      begun. It is the only one of the three that changes what an existing
--      connection is allowed to do.
--
--   3. ONLY `pg_terminate_backend` CLOSES THE CONNECTION. Neither of the above
--      does; the socket stays up until something tears it down.
--
-- WHY ALL THREE, AND NOT "TERMINATE IS ENOUGH". Terminating looks sufficient
-- until you notice that it is a POINT-IN-TIME SCAN inside a transaction, while
-- the catalog changes around it only take effect at COMMIT. So there is a real
-- window — measured, not hypothesised (see
-- `test_a_connection_opened_in_the_terminate_window_is_left_powerless`):
--
--     t0  the scan runs; every backend of the role alive at t0 is signalled
--     t1  the host reconnects. `NOLOGIN` is not committed yet, so it is
--         ADMITTED — and it did not exist at t0, so nothing signalled it
--     t2  COMMIT. The role is now NOLOGIN, but the t1 connection is open,
--         authenticated, and — under 0003 — still holds every `aicc_worker`
--         privilege, for as long as it cares to keep the socket open.
--
-- Under 0003 that survivor is a fully privileged worker session belonging to a
-- host an operator has just revoked. Nothing terminates it, because terminating
-- is the thing that already happened. The REVOKE is what makes it powerless,
-- and it is powerless from its very next statement, without any reconnect. That
-- is the whole content of this slice: revocation has to change what an open
-- connection MAY DO, not only whether a new one may be made.
--
-- The identity gate is the same shape one layer up: `identity_assert()` is
-- called per privileged statement with no cache, so a revoked CREDENTIAL is
-- refused on the connection it is presented from, at the next statement.
-- Measured end to end on this cluster, from the revoking transaction's commit
-- to the first refusal observed on a connection that was already open and was
-- never re-established: single-digit milliseconds, and it is one statement
-- round trip, because there is nothing else in it.
--
-- WHY THE REVOKE NAMES `aicc_worker` AND DOES NOT LOOP OVER EVERY MEMBERSHIP.
-- A per-host role is created by `identity_create_worker_role()` as
-- `CREATE ROLE ... NOLOGIN IN ROLE aicc_worker` and is granted nothing else,
-- ever: that one membership is the entire carrier of its privileges, which
-- `test_a_per_host_role_holds_nothing_but_the_one_membership` pins so the claim
-- cannot rot. A generic "revoke every membership" loop would look stronger and
-- be weaker — the migrator holds `ADMIN` on `aicc_worker` and on nothing else,
-- so a membership added by hand to some other group would raise, and a raise
-- here aborts the whole disable, leaving the role logged in AND privileged.
-- Naming the role keeps the grant and the revoke in the same two places,
-- spelling the same name.


-- ---------------------------------------------------------------------------
-- identity_disable_role(): now all three mechanisms
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION identity_disable_role(p_role text) RETURNS integer
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_killed integer;
BEGIN
    IF p_role IS NULL THEN RETURN 0; END IF;

    -- (2) PRIVILEGES — the one that reaches sessions already open, and the one
    -- 0003 was missing. Takes effect for every backend of this role at COMMIT,
    -- on their next statement.
    EXECUTE format('REVOKE %I FROM %I', 'aicc_worker', p_role);

    -- (1) FUTURE AUTHENTICATION. Evaluated only when a connection is made, so
    -- this reaches nobody who is already here.
    EXECUTE format('ALTER ROLE %I NOLOGIN VALID UNTIL %L', p_role, '-infinity');

    -- (3) CURRENT SESSIONS. Blunt on purpose: every backend of the role dies.
    -- Narrowing by `application_name` is NOT available as a control, because it
    -- is client-settable — it may be used for diagnostics, never for a
    -- targeting or authorisation decision.
    --
    -- LAST of the three, and that ordering is the only one that matters here.
    -- The two catalog changes above are transactional and become visible
    -- together at commit, so their order relative to each other is invisible;
    -- `pg_terminate_backend` is NOT transactional — the signal is away the
    -- instant this runs and is not taken back by a rollback. Running it last
    -- makes the scan-to-commit window in which a reconnect can slip through as
    -- short as this function can make it. It does not close it, which is why
    -- (2) exists.
    --
    -- `pid <> pg_backend_pid()` stays: a function that terminated its own
    -- backend would roll back the revocation it is in the middle of writing.
    SELECT count(*) INTO v_killed FROM (
        SELECT pg_terminate_backend(pid) FROM pg_stat_activity
         WHERE usename = p_role AND pid <> pg_backend_pid()) t;
    RETURN v_killed;
END
$$;


-- ---------------------------------------------------------------------------
-- identity_enable_role(): the inverse, so disabling is not a one-way door
-- ---------------------------------------------------------------------------
-- Withdrawing the membership means readmission has to put it back, or a
-- re-enrolled host would authenticate successfully and then be refused by every
-- statement it tried — LOGIN restored, privileges not. That failure would be
-- loud rather than dangerous, but it would also be an outage per rebuilt host.
--
-- Granted to no role, like the other role-DDL functions: it is reachable only
-- from `identity_issue_db_credential()`, under the same `kind = 'worker_host'`
-- guard that `identity_revoke_credential()` uses on the way down. One
-- condition, spelled the same way at both ends, so the two cannot drift.
--
-- It restores exactly `aicc_worker` and nothing else. If an operator had
-- hand-added the role to some other group before the incident, that does not
-- come back by itself — re-granting privileges a human granted outside the
-- protocol should require the human, not a rebuild.
CREATE FUNCTION identity_enable_role(p_role text) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_role IS NULL THEN RETURN; END IF;
    EXECUTE format('GRANT %I TO %I', 'aicc_worker', p_role);
END
$$;


-- ---------------------------------------------------------------------------
-- identity_issue_db_credential(): re-grant on the way back in
-- ---------------------------------------------------------------------------
-- Unchanged from 0003 except for the `identity_enable_role()` call beside the
-- existing `identity_set_role_secret()` one. The pair is deliberate: those two
-- statements are "this role may connect again" and "this role may do something
-- again", and after this migration neither is implied by the other.
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

    -- Strictly-lower-trust rule: nobody may mint a credential at or above its
    -- own tier. That is what stops a compromised worker issuing itself a
    -- control-plane credential. SELF-issuance is exempt, because a worker and
    -- itself are the same tier and rotation must remain possible: the caller
    -- already proved possession of the current secret and the TTL is still
    -- clamped, so it can change WHICH secret works and nothing else. A caller
    -- with no principal row is the provisioning path, which runs before any
    -- principal exists.
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

    -- Clamped here rather than trusted from the argument, for the same reason
    -- 0002 recomputes an attempt's visibility deadline: a caller must not be
    -- able to widen its own lifetime. One hour, because revocation cannot reach
    -- a partitioned host — `pg_terminate_backend` needs a connection to
    -- terminate — so the effective revocation latency for such a host is
    -- bounded by this TTL and by nothing else.
    v_ttl := least(coalesce(p_ttl, interval '15 minutes'), interval '1 hour');

    -- PostgreSQL stores exactly ONE verifier per role, so leaving the previous
    -- credential live would make this table disagree with `pg_authid`.
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

    -- Worker hosts only, mirroring `identity_revoke_credential()`. The
    -- operator's and the control plane's roles are provisioned outside this
    -- protocol and are never disabled by it, so there is nothing to restore for
    -- them — and granting them `aicc_worker` on a routine credential issue
    -- would be a widening, not a restore.
    IF p.kind = 'worker_host' THEN
        PERFORM identity_enable_role(p.db_role);
    END IF;
    PERFORM identity_set_role_secret(p.db_role, p_scram_verifier, now() + v_ttl);

    PERFORM _principal_audit(p_principal_id, 'issue', 'granted', v_id, NULL,
                             jsonb_build_object('ttl', v_ttl::text));
    RETURN QUERY SELECT v_id, NULL::text;
END
$$;


-- ---------------------------------------------------------------------------
-- identity_revoke_credential(): record what the revocation actually closed
-- ---------------------------------------------------------------------------
-- Unchanged from 0003 except that the audit row is now written AFTER the role
-- is disabled and carries the outcome — how many live backends the revocation
-- closed, and whether the privilege withdrawal ran at all.
--
-- The reason to bother: "we revoked it" and "it stopped being able to do
-- things" are different claims, and during an incident the second is the one
-- being asked. `killed = 0` on a host that was known to be connected is a
-- finding, not a formality, and without this it is not recorded anywhere a
-- query can reach — the server log line is not a queryable security record.
CREATE OR REPLACE FUNCTION identity_revoke_credential(p_credential_id text, p_reason text)
    RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE c principal_credential%ROWTYPE; p principal%ROWTYPE; v_killed integer;
BEGIN
    SELECT * INTO c FROM principal_credential
      WHERE credential_id = p_credential_id FOR UPDATE;
    IF NOT FOUND OR c.revoked_at IS NOT NULL THEN
        RETURN false;   -- idempotent: revoking twice is not an error
    END IF;

    UPDATE principal_credential
       SET revoked_at = now(), revoke_reason = p_reason,
           revoked_by = current_principal(), updated_at = now()
     WHERE credential_id = p_credential_id;

    SELECT * INTO p FROM principal WHERE principal_id = c.principal_id;
    -- Worker hosts only. The operator's and the control plane's roles are
    -- provisioned outside this protocol and are shared with everything else
    -- those components do, so disabling one because a credential lapsed would
    -- turn a routine expiry into an outage of the control plane itself.
    IF p.kind = 'worker_host' THEN
        v_killed := identity_disable_role(p.db_role);
    END IF;
    PERFORM _principal_audit(c.principal_id, 'revoke', 'granted', c.credential_id,
             p_reason,
             CASE WHEN p.kind = 'worker_host'
                  THEN jsonb_build_object('db_role', p.db_role,
                                          'login_disabled', true,
                                          'membership_revoked', true,
                                          'backends_terminated', v_killed)
             END);
    RETURN true;
END
$$;
