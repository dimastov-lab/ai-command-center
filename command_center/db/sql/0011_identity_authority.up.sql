-- The control plane becomes an identity, and then becomes the authority
-- (VOYN-W0-AICC-SRV-02-MIGRATION, expand phase).
--
-- 0003 built the whole identity layer — `principal`, `principal_credential`,
-- `identity_assert()`, the enrolment protocol — and 0010 made revocation reach
-- an open session. What none of them did is POPULATE it. A deployed database
-- today holds ZERO rows in `principal`, because the only function that can
-- create the first ones, `identity_bootstrap_principal()`, is granted to no
-- role and called from nowhere: `grep -rn identity_bootstrap_principal
-- --include=*.py` returns tests and nothing else.
--
-- That is not a cosmetic gap, and it is worth being precise about which half of
-- it bites, because the two halves fail in OPPOSITE directions.
--
--   FAILS CLOSED, and is why the enrolment protocol is unusable today.
--   `enroll_mint_ticket()` opens with `IF current_principal() IS NULL THEN
--   ... RETURN 'no_principal'`. `current_principal()` resolves `session_user`
--   against `principal.db_role`, so with no rows it is NULL for `aicc_app`,
--   and the control plane cannot mint a ticket at all. Every SRV-03 path
--   starts at a mint. The protocol shipped and cannot be driven.
--
--   FAILS OPEN, and is the security-relevant one.
--   `identity_issue_db_credential()` guards the strictly-lower-trust rule with
--   `IF v_caller IS NOT NULL AND v_caller IS DISTINCT FROM p_principal_id`.
--   A caller with NO principal row skips the tier check entirely — not
--   because it was allowed, but because there was nothing to compare. The
--   rule that stops a compromised worker minting itself a control-plane
--   credential is, in an unpopulated database, unreachable code.
--
-- Both are fixed by the same act: give the infrastructure roles principals.
-- This migration is the machinery that makes doing so SAFE TO ROLL OUT, which
-- is the entire content of the slice.
--
-- =========================================================================
-- WHY A MODE AT ALL, AND WHY THE ROLLOUT IS THREE STEPS AND NOT ONE
-- =========================================================================
-- Closing the fail-open above is a one-line change, and shipping it as one
-- line would be the mistake. The moment `current_principal() IS NULL` becomes
-- a refusal, every caller that has no principal row stops working — and which
-- callers those are is a property of PROVISIONING, not of this repository. A
-- database whose `identity_bootstrap_principal()` step was skipped, or run with
-- a `db_role` that does not match the `AICC_PG_USER` the process actually
-- connects as, looks exactly like a healthy one until the first credential
-- issue is refused.
--
-- So the rollout is expand-contract, and the three steps are:
--
--   1. SCHEMA (this migration). `identity_policy` arrives holding
--      'advisory'. Nothing changes behaviour: the fail-open still fails open,
--      but it now WRITES DOWN that it did.
--   2. POPULATE, IN ADVISORY MODE. `python -m command_center.db
--      identity-bootstrap` inserts the `control_plane` and `operator`
--      principals. If the mapping is wrong, the advisory audit says so —
--      `principal_event` grows `policy`/`no_principal_advisory` rows naming
--      the `session_user` that had no principal — and nothing is refused.
--   3. AUTHORITATIVE. `identity-mode --enforcement authoritative`, run by the
--      operator, flips the row. The same condition now refuses.
--
-- ROLLBACK is step 3 in reverse and needs no migration, no deploy of code, and
-- no schema change: `AICC_IDENTITY_ENFORCEMENT=advisory` and re-run
-- `identity-mode`. That is the property the mode exists to buy. A rollout whose
-- only reverse gear is `migrate down` is one that will be ridden out instead of
-- reversed at 3am, and riding it out means refusing credential issues.
--
-- =========================================================================
-- WHY THE MODE IS A TABLE AND NOT A GUC
-- =========================================================================
-- The obvious implementation is a session GUC — `SET aicc.identity_mode` from
-- the pool at checkout, read with `current_setting()`. It is rejected, and for
-- the reason 0010 already states about `application_name`: a GUC is
-- CLIENT-SETTABLE. `aicc_worker` holds EXECUTE on `identity_assert()` and
-- `enroll_rotate_self()`, so a compromised execution host that could also
-- `SET aicc.identity_mode = 'advisory'` would be turning off, for its own
-- session, the check that governs it. A switch that disables an authorisation
-- control must not be reachable by the party the control is aimed at.
--
-- A table is not client-settable. `identity_policy` is granted SELECT to the
-- app and the operator, INSERT/UPDATE/DELETE to nobody, and the only writer is
-- `identity_set_enforcement()` — SECURITY DEFINER, granted to `aicc_operator`
-- alone. That the control plane cannot weaken its own gate is the same shape as
-- 0003 keeping `identity_revoke_principal` off `aicc_app`: a compromised
-- control plane may do damage within the protocol and may not change what the
-- protocol is.
--
-- The env var is therefore the DEPLOYMENT's expression of intent, not the
-- mechanism. `AICC_IDENTITY_ENFORCEMENT` is read by a CLI step that connects as
-- the operator and calls the function; the database, not the process
-- environment, remains the authority. A rollback is an operator action by
-- construction, which is the correct friction for "turn the identity gate off".
--
-- =========================================================================
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DO — THE HARD STOP
-- =========================================================================
-- The contract half of expand-contract is NOT performed here, and the reason is
-- not sequencing taste. It is that the contract step cannot be executed
-- correctly yet by anyone, including by hand.
--
-- Contracting means retiring the shared `aicc_worker` password: every execution
-- host authenticating as its own `aicc_w_*` role, and the fleet-wide credential
-- ceasing to exist. SRV-03 landed the PROTOCOL for that in this schema
-- (`enroll_mint_ticket` / `enroll_redeem_ticket` / `enroll_rotate_self`) and it
-- has NO CALLER: those three names appear in `tests/` and in the grant matrix,
-- and nowhere in `command_center/`. There is no code path that redeems a ticket
-- and configures a worker's connection from the result.
--
-- So the per-host credentials the contract step depends on cannot be DELIVERED.
-- The only remaining way to give thirty hosts thirty secrets is for a human to
-- paste them, which produces exactly the long-lived hand-distributed secret the
-- shared password already is — the same defect, multiplied by the fleet size
-- and with no rotation story. Contracting now would be a downgrade wearing the
-- vocabulary of an upgrade.
--
-- The named removal step, its two negative tests and the conditions that
-- release it are written down in `docs/identity-authority-rollout.md`. The
-- tests are not deferred with it: they run today against a throwaway database
-- in `tests/db/test_identity_contract_rehearsal.py`, which APPLIES the contract
-- and asserts the two negatives, so the step is known to be correct and
-- executable on the day it is released rather than discovered to be wrong then.


-- ---------------------------------------------------------------------------
-- identity_policy
-- ---------------------------------------------------------------------------
-- Exactly one row, forever. `singleton boolean PRIMARY KEY CHECK (singleton)`
-- is the whole enforcement: the CHECK admits only `true` and the PRIMARY KEY
-- admits `true` once, so a second row is refused by the database rather than by
-- a convention about which row is the real one. A mode that could be read from
-- two rows is a mode that can disagree with itself.
CREATE TABLE identity_policy (
    singleton   boolean     PRIMARY KEY DEFAULT true,

    -- 'advisory'      — the gate observes and records; nothing is refused.
    -- 'authoritative' — the gate refuses.
    enforcement text        NOT NULL,

    -- `session_user` of the last flip, not `current_principal()`: this column
    -- must stay meaningful in advisory mode, which is precisely the state in
    -- which principals may not exist yet.
    changed_by  text,
    changed_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL,

    CONSTRAINT identity_policy_singleton CHECK (singleton),
    CONSTRAINT identity_policy_enforcement_valid
        CHECK (enforcement IN ('advisory', 'authoritative'))
);

-- 'advisory' on arrival, which is the only value that makes this migration
-- behaviour-preserving. A migration that landed 'authoritative' would refuse
-- credential issues on every database whose bootstrap step had not been run
-- yet — i.e. all of them, since this migration is what introduces the step.
INSERT INTO identity_policy (singleton, enforcement, changed_by, changed_at,
                             created_at, updated_at)
VALUES (true, 'advisory', session_user, now(), now(), now());


-- ---------------------------------------------------------------------------
-- identity_enforcement()
-- ---------------------------------------------------------------------------
-- FAILS CLOSED on a missing row, and that asymmetry is deliberate. No role
-- holds DELETE on any table in this schema, so an absent row is not a state the
-- protocol can reach — it is evidence of tampering or of a partial restore. The
-- safe reading of "the policy is gone" is 'authoritative': absence of a policy
-- must not be a way to obtain absence of enforcement, which is what
-- `coalesce(..., 'advisory')` would have made it.
CREATE FUNCTION identity_enforcement() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT coalesce((SELECT enforcement FROM identity_policy), 'authoritative')
$$;


-- ---------------------------------------------------------------------------
-- identity_set_enforcement()
-- ---------------------------------------------------------------------------
-- The only writer of `identity_policy`, granted to `aicc_operator` and to
-- nothing else. RAISEs on an unknown mode rather than returning a reason,
-- unlike the enrolment surface: the refusal paths there return so the caller
-- can commit the audit row that records a DENIAL, and there is no denial here
-- to preserve — a typo'd mode is a broken operator command, not a security
-- event, and aborting is the outcome that cannot be ignored by a script.
--
-- Returns the PREVIOUS mode so the caller can tell a flip from a no-op, which
-- is what makes the CLI able to report "already authoritative" instead of
-- claiming a change it did not make.
CREATE FUNCTION identity_set_enforcement(p_mode text) RETURNS text
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_prev text;
BEGIN
    IF p_mode IS NULL OR p_mode NOT IN ('advisory', 'authoritative') THEN
        RAISE EXCEPTION 'unknown identity enforcement mode: %',
            coalesce(p_mode, '<null>');
    END IF;

    -- `FOR UPDATE` so two concurrent flips serialise and the audit records the
    -- transitions in the order they actually happened, rather than two rows
    -- both claiming to have come from the same previous value.
    SELECT enforcement INTO v_prev FROM identity_policy FOR UPDATE;

    UPDATE identity_policy
       SET enforcement = p_mode, changed_by = session_user,
           changed_at = now(), updated_at = now();

    -- Attributed to the caller's principal when there is one and to NULL when
    -- there is not — the second case being the expected one for the flip that
    -- happens before bootstrap. `_principal_audit` records `session_user`
    -- either way, so the row is never anonymous.
    PERFORM _principal_audit(current_principal(), 'policy', 'granted', NULL, NULL,
             jsonb_build_object('setting', 'enforcement',
                                'from', v_prev, 'to', p_mode));
    RETURN v_prev;
END
$$;


-- ---------------------------------------------------------------------------
-- identity_issue_db_credential(): the fail-open, closed
-- ---------------------------------------------------------------------------
-- Identical to 0010's definition except for the `v_caller IS NULL` branch,
-- which 0010 and 0003 both left implicit. Restated in full rather than patched,
-- because `CREATE OR REPLACE FUNCTION` has no partial form: reproducing 0010's
-- body is what stops this migration silently reverting the
-- `identity_enable_role()` call that 0010 added to it.
--
-- The new branch is the whole slice at the statement level:
--
--   advisory       record that the tier rule could not be applied, and issue.
--                  The audit row is the observation the rollout is FOR: it
--                  names the `session_user` that has no principal, which is
--                  the exact input the bootstrap step needs to be correct.
--   authoritative  refuse. An unenrolled caller is no longer a caller the
--                  tier rule declines to have an opinion about; it is one the
--                  rule has no basis to trust.
--
-- Note where the branch sits: BEFORE the self-issuance exemption, not after.
-- Self-issuance is exempt from the tier comparison because a principal and
-- itself are the same tier, but that exemption presupposes a principal. A
-- caller with none is not "issuing to itself"; it is unidentified, and letting
-- it through the self-branch would reintroduce the fail-open under a new name.
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

    IF v_caller IS NULL THEN
        -- The provisioning path reaches here legitimately: the very first
        -- principals are created before any principal exists to be the caller.
        -- That is why this is a MODE and not an unconditional refusal — the
        -- database cannot tell provisioning from a gap in it, but the operator
        -- running the rollout can, and the advisory window is where they look.
        IF identity_enforcement() = 'authoritative' THEN
            PERFORM _principal_audit(p_principal_id, 'issue', 'rejected', NULL,
                     'no_principal',
                     jsonb_build_object('session_user', session_user,
                                        'enforcement', 'authoritative'));
            RETURN QUERY SELECT NULL::text, 'no_principal'::text;
            RETURN;
        END IF;
        PERFORM _principal_audit(p_principal_id, 'issue', 'granted', NULL,
                 'no_principal_advisory',
                 jsonb_build_object('session_user', session_user,
                                    'enforcement', 'advisory',
                                    'would_refuse', true,
                                    'target_tier', p.trust_tier));
    ELSIF v_caller IS DISTINCT FROM p_principal_id THEN
        -- Strictly-lower-trust rule: nobody may mint a credential at or above
        -- its own tier. That is what stops a compromised worker issuing itself
        -- a control-plane credential. SELF-issuance is exempt, because a worker
        -- and itself are the same tier and rotation must remain possible: the
        -- caller already proved possession of the current secret and the TTL is
        -- still clamped, so it can change WHICH secret works and nothing else.
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
