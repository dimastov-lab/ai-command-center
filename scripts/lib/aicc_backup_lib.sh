#!/usr/bin/env bash
# Encryption and off-host transport helpers for the AICC database backup
# scripts (VOYN-W0-AICC-SRV-08).
#
# Sourced, not executed. Lives in one file because backup, restore and verify
# must agree byte-for-byte on the archive layout: get the recipient handling
# right in one place and the three scripts cannot drift into producing files
# each other cannot read.
#
# Two backends, chosen by which recipient flag the caller passed:
#
#   age  — the default recommendation. The backup host holds only a public
#          recipient key, so a host compromise yields ciphertext and no way to
#          read it. Identities live off-host with the operator.
#   gpg  — for shops whose off-host key custody is already a smartcard or an
#          existing OpenPGP escrow. Same public-key property.
#
# Neither backend signs. age has no signing at all, and the encryption we do
# with gpg is not a signature either: an attacker who can write to the off-host
# store can *replace* an archive with one they encrypted to the same public
# key. What encryption buys is confidentiality plus detection of modified
# ciphertext (both backends are AEAD). Authenticity of the producer is the
# storage layer's job — append-only buckets, object lock, or a pull-based
# collector the database host cannot reach. `docs/operations/encrypted-backups.md`
# spells this out.

# ---------------------------------------------------------------------------
# Encryption backend
# ---------------------------------------------------------------------------

AICC_AGE_RECIPIENTS=()
AICC_AGE_RECIPIENT_FILES=()
AICC_AGE_IDENTITY_FILES=()
AICC_GPG_RECIPIENTS=()
AICC_CRYPTO_BACKEND=""

crypto_add_age_recipient()      { AICC_AGE_RECIPIENTS+=("$1"); }
crypto_add_age_recipient_file() { AICC_AGE_RECIPIENT_FILES+=("$1"); }
crypto_add_age_identity()       { AICC_AGE_IDENTITY_FILES+=("$1"); }
crypto_add_gpg_recipient()      { AICC_GPG_RECIPIENTS+=("$1"); }

# Decide the backend from the recipients supplied, and fail loudly rather than
# quietly picking one when both were given: an operator who passed both flags
# has a mistaken mental model of which key protects the archive, and silently
# honouring one of them hides that until a restore.
crypto_resolve_encrypt_backend() {
    local have_age=0 have_gpg=0
    if (( ${#AICC_AGE_RECIPIENTS[@]} + ${#AICC_AGE_RECIPIENT_FILES[@]} > 0 )); then
        have_age=1
    fi
    if (( ${#AICC_GPG_RECIPIENTS[@]} > 0 )); then
        have_gpg=1
    fi

    if (( have_age && have_gpg )); then
        echo "both age and gpg recipients were given; pick one backend" >&2
        return 2
    fi
    if (( have_age )); then
        AICC_CRYPTO_BACKEND="age"
    elif (( have_gpg )); then
        AICC_CRYPTO_BACKEND="gpg"
    else
        AICC_CRYPTO_BACKEND=""
        return 1
    fi

    command -v "$AICC_CRYPTO_BACKEND" >/dev/null || {
        echo "${AICC_CRYPTO_BACKEND} not found in PATH; install it or choose the other backend" >&2
        return 127
    }

    local f
    for f in ${AICC_AGE_RECIPIENT_FILES[@]+"${AICC_AGE_RECIPIENT_FILES[@]}"}; do
        [[ -r "$f" ]] || { echo "age recipients file not readable: ${f}" >&2; return 2; }
    done
    return 0
}

crypto_suffix() {
    case "$AICC_CRYPTO_BACKEND" in
        age) echo ".age" ;;
        gpg) echo ".gpg" ;;
        *)   echo "" ;;
    esac
}

# stdin -> $1. The plaintext dump never becomes a file on the backup host.
crypto_encrypt_to() {
    local out="$1" args=() r

    case "$AICC_CRYPTO_BACKEND" in
        age)
            for r in ${AICC_AGE_RECIPIENTS[@]+"${AICC_AGE_RECIPIENTS[@]}"}; do
                args+=(--recipient "$r")
            done
            for r in ${AICC_AGE_RECIPIENT_FILES[@]+"${AICC_AGE_RECIPIENT_FILES[@]}"}; do
                args+=(--recipients-file "$r")
            done
            age "${args[@]}" --output "$out"
            ;;
        gpg)
            for r in ${AICC_GPG_RECIPIENTS[@]+"${AICC_GPG_RECIPIENTS[@]}"}; do
                args+=(--recipient "$r")
            done
            # --trust-model always: a backup recipient key is trusted because the
            # operator named it on the command line, not because a web of trust
            # says so, and an unattended nightly job cannot answer a trust prompt.
            # --compress-algo none: pg_dump already compressed at level 9, so
            # gpg's own pass is pure CPU for no bytes saved.
            gpg --batch --yes --quiet --trust-model always --compress-algo none \
                "${args[@]}" --encrypt --output "$out"
            ;;
        *)
            echo "crypto_encrypt_to called with no backend selected" >&2
            return 1
            ;;
    esac
}

# $1 (ciphertext) -> $2 (plaintext), using the identity this host holds.
crypto_decrypt_to() {
    local in="$1" out="$2" args=() i

    case "$AICC_CRYPTO_BACKEND" in
        age)
            (( ${#AICC_AGE_IDENTITY_FILES[@]} > 0 )) || {
                echo "an age-encrypted archive needs --age-identity <file>" >&2
                return 2
            }
            for i in ${AICC_AGE_IDENTITY_FILES[@]+"${AICC_AGE_IDENTITY_FILES[@]}"}; do
                [[ -r "$i" ]] || { echo "age identity not readable: ${i}" >&2; return 2; }
                args+=(--identity "$i")
            done
            age --decrypt "${args[@]}" --output "$out" "$in"
            ;;
        gpg)
            # The secret key comes from the keyring / agent, so GNUPGHOME (and a
            # smartcard, if that is where the key lives) is the operator's knob.
            gpg --batch --yes --quiet --decrypt --output "$out" "$in"
            ;;
        *)
            echo "crypto_decrypt_to called with no backend selected" >&2
            return 1
            ;;
    esac
}

# pg_dump custom-format archives begin with this magic. Anything that does not
# is either encrypted or not a dump at all — which is the distinction restore
# needs before it decides whether to reach for a key.
AICC_PGDUMP_MAGIC="PGDMP"
AICC_AGE_MAGIC="age-encryption.org/v1"

# Echo "age" | "gpg" | "plain", or fail if the file is none of them.
#
# Content first, suffix second: a `.dump` that is actually age ciphertext (an
# operator's rename) must not be fed to pg_restore as if it were an archive,
# and a `.age` suffix on a plaintext dump must not send us hunting for a key.
crypto_detect_file() {
    local path="$1" head
    head="$(head -c 21 -- "$path" 2>/dev/null || true)"

    if [[ "$head" == "$AICC_AGE_MAGIC" ]]; then
        echo "age"; return 0
    fi
    if [[ "$head" == "$AICC_PGDUMP_MAGIC"* ]]; then
        echo "plain"; return 0
    fi
    # OpenPGP has no ASCII magic; its binary framing is a packet tag byte, which
    # is too weak to assert on. Ask gpg instead — it parses the packet stream
    # without a secret key. It exits non-zero once it reaches the undecryptable
    # body, so the packet list, not the status, is the answer.
    if command -v gpg >/dev/null &&
       gpg --batch --quiet --list-packets -- "$path" 2>/dev/null | grep -q "pubkey enc packet"; then
        echo "gpg"; return 0
    fi
    if [[ "$head" == "-----BEGIN PGP MESSAGE"* ]]; then
        echo "gpg"; return 0
    fi

    echo "unrecognised archive format: ${path}" >&2
    echo "expected a pg_dump custom archive, an age file, or an OpenPGP message" >&2
    return 2
}

# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------

sha256_of() {
    if command -v sha256sum >/dev/null; then
        sha256sum -- "$1" | awk '{print $1}'
    else
        shasum -a 256 -- "$1" | awk '{print $1}'
    fi
}

sha256_of_stdin() {
    if command -v sha256sum >/dev/null; then
        sha256sum | awk '{print $1}'
    else
        shasum -a 256 | awk '{print $1}'
    fi
}

# ---------------------------------------------------------------------------
# Off-host transport
# ---------------------------------------------------------------------------
#
# One dependency: ssh. Copying with `ssh host "cat > tmp && mv tmp final"`
# rather than scp/rsync buys the same atomic-rename guarantee the local write
# already has — a collector that lists the destination never sees a name that
# looks like a finished archive but is still being written. AICC_BACKUP_SSH
# overrides the command (extra options, a jump host, a test double).

aicc_ssh() {
    local -a ssh_cmd
    read -r -a ssh_cmd <<< "${AICC_BACKUP_SSH:-ssh}"
    "${ssh_cmd[@]}" "$@"
}

# `[user@]host:path` — the scp form. A colon that appears after a slash is part
# of a local path, not a host separator, so `/mnt/a:b/backups` stays local.
offhost_is_remote() {
    [[ "$1" == *:* && "${1%%:*}" != *"/"* && -n "${1%%:*}" ]]
}

offhost_host() { echo "${1%%:*}"; }
offhost_path() { echo "${1#*:}"; }

# Remote commands are assembled as shell text, so a quote in the path would let
# the destination string run commands on the collector. The path comes from an
# operator's flag rather than from user input, but "it is only ever operators"
# is how a backup destination read out of a config file becomes an injection.
offhost_assert_safe_path() {
    case "$1" in
        *\'*|*$'\n'*)
            echo "off-host path may not contain quotes or newlines: ${1}" >&2
            return 2
            ;;
    esac
}

# Copy $1 to <$2>/<basename $1>. $2 is a directory spec, local or remote.
offhost_put() {
    local src="$1" dest_dir="$2" name
    name="$(basename -- "$src")"

    if offhost_is_remote "$dest_dir"; then
        local host path
        host="$(offhost_host "$dest_dir")"
        path="$(offhost_path "$dest_dir")"
        offhost_assert_safe_path "$path" || return 2
        aicc_ssh "$host" \
            "umask 077 && mkdir -p '${path}' && cat > '${path}/${name}.partial' \
             && mv -f '${path}/${name}.partial' '${path}/${name}'" < "$src"
    else
        mkdir -p -- "$dest_dir"
        chmod 700 -- "$dest_dir" 2>/dev/null || true
        cp -- "$src" "${dest_dir}/${name}.partial"
        chmod 600 -- "${dest_dir}/${name}.partial"
        mv -f -- "${dest_dir}/${name}.partial" "${dest_dir}/${name}"
    fi
}

# SHA-256 of a file *as the destination sees it*. Computed there and read back,
# so what is compared is the bytes that landed — not rsync's opinion that the
# transfer went fine.
offhost_digest() {
    local spec="$1"

    if offhost_is_remote "$spec"; then
        local host path
        host="$(offhost_host "$spec")"
        path="$(offhost_path "$spec")"
        offhost_assert_safe_path "$path" || return 2
        aicc_ssh "$host" \
            "if command -v sha256sum >/dev/null 2>&1; then sha256sum -- '${path}'; \
             else shasum -a 256 -- '${path}'; fi" | awk '{print $1}'
    else
        sha256_of "$spec"
    fi
}

offhost_get() {
    local spec="$1" out="$2"

    if offhost_is_remote "$spec"; then
        local host path
        host="$(offhost_host "$spec")"
        path="$(offhost_path "$spec")"
        offhost_assert_safe_path "$path" || return 2
        aicc_ssh "$host" "cat -- '${path}'" > "$out"
    else
        [[ -f "$spec" ]] || { echo "archive not found: ${spec}" >&2; return 2; }
        cp -- "$spec" "$out"
    fi
}

offhost_exists() {
    local spec="$1"

    if offhost_is_remote "$spec"; then
        local host path
        host="$(offhost_host "$spec")"
        path="$(offhost_path "$spec")"
        offhost_assert_safe_path "$path" || return 2
        aicc_ssh "$host" "test -f '${path}'"
    else
        [[ -f "$spec" ]]
    fi
}

# ---------------------------------------------------------------------------
# Metadata sidecar
# ---------------------------------------------------------------------------
#
# `<archive>.sha256` covers the ciphertext: it is what the destination can
# check without holding a key, so it is the transport integrity proof.
# `<archive>.meta` additionally records the digest of the *plaintext* dump,
# computed on the backup host as the stream went by. That is what makes a
# restore end-to-end verifiable: matching it after decryption proves the bytes
# pg_restore is about to read are the bytes pg_dump produced, rather than a
# well-formed archive that decrypted from something else.

meta_read() {
    local meta_file="$1" key="$2"
    [[ -f "$meta_file" ]] || return 1
    sed -n "s/^${key}=//p" "$meta_file" | head -n 1
}
