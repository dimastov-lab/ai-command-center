#!/usr/bin/env bash
# Back up the AICC server database, encrypted and off-host
# (VOYN-W0-AICC-SRV-01a, hardened by VOYN-W0-AICC-SRV-08).
#
# Produces a pg_dump custom-format archive that is encrypted to a public key
# before it ever reaches a disk, a SHA-256 sidecar over the ciphertext, and a
# `.meta` sidecar recording the SHA-256 of the plaintext dump. Custom format
# rather than plain SQL because it restores selectively and in parallel, and
# because pg_restore validates the archive's own structure — a truncated
# plain-SQL dump looks like a valid, shorter database.
#
# Encryption is public-key and mandatory. This host holds a *recipient* key
# only; the identity that can decrypt lives elsewhere, so an attacker who owns
# the database host walks away with ciphertext and no way to read it. That is
# the whole point, and it is why `--verify` here cannot run pg_restore over the
# result: use `aicc_pg_verify_backup.sh` on the host that holds the identity.
# `--no-encrypt` exists for a local throwaway dump and says so loudly.
#
# `--offhost-dest` copies the archive and both sidecars to a second location —
# a mounted store or `[user@]host:/path` over ssh — and then verifies the copy
# by computing its SHA-256 *at the destination* and comparing. A backup that
# only exists on the machine it was taken from is not a backup.
#
# Retention (`--keep`) prunes the local directory only. Deleting off-host
# copies is deliberately not something this host can do: an attacker who
# reaches the database host must not be able to reach round and erase the
# backups. Expire them with the storage layer's own lifecycle policy.
#
# Credentials come from the environment (AICC_PG_*) and are handed to libpq via
# PGPASSWORD, which is never echoed. Nothing here writes a password to disk.
#
# Usage:
#   scripts/aicc_pg_backup.sh --out-dir /var/backups/aicc \
#       --age-recipient age1qz... \
#       --offhost-dest backups@vault.internal:/srv/aicc \
#       [--verify] [--keep 14]
#
#   Recipients:  --age-recipient KEY | --age-recipients-file FILE
#                --gpg-recipient KEY-OR-FINGERPRINT
#                --no-encrypt   (writes plaintext; prints a warning)

set -euo pipefail

# shellcheck source=scripts/lib/aicc_backup_lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/aicc_backup_lib.sh"

OUT_DIR=""
KEEP=""
VERIFY=0
NO_ENCRYPT=0
OFFHOST_DEST=""

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir)             OUT_DIR="${2:?--out-dir needs a path}"; shift 2 ;;
        --keep)                KEEP="${2:?--keep needs a count}"; shift 2 ;;
        --verify)              VERIFY=1; shift ;;
        --age-recipient)       crypto_add_age_recipient "${2:?--age-recipient needs a key}"; shift 2 ;;
        --age-recipients-file) crypto_add_age_recipient_file "${2:?--age-recipients-file needs a path}"; shift 2 ;;
        --gpg-recipient)       crypto_add_gpg_recipient "${2:?--gpg-recipient needs a key}"; shift 2 ;;
        --no-encrypt)          NO_ENCRYPT=1; shift ;;
        --offhost-dest)        OFFHOST_DEST="${2:?--offhost-dest needs a destination}"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

[[ -n "$OUT_DIR" ]] || { echo "--out-dir is required" >&2; exit 2; }

# Validated before use because the retention step feeds it to `tail -n +N` via
# bash arithmetic, where a non-numeric value evaluates to 0 and would delete
# every archive — including the one just written.
if [[ -n "$KEEP" && ! "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
    echo "--keep must be a positive integer; got '${KEEP}'" >&2
    exit 2
fi

# The failure mode this guards against is the one that matters: a nightly job
# whose --age-recipient came from an unset variable, quietly writing readable
# copies of every row in the system to disk for months. Unencrypted is
# reachable, but only by asking for it in as many words.
if (( NO_ENCRYPT )); then
    # Counted rather than resolved: a recipient naming a backend that is not
    # installed is still a contradictory instruction, and resolving would report
    # that missing tool instead of the contradiction.
    if (( ${#AICC_AGE_RECIPIENTS[@]} + ${#AICC_AGE_RECIPIENT_FILES[@]} \
          + ${#AICC_GPG_RECIPIENTS[@]} > 0 )); then
        echo "--no-encrypt was passed together with a recipient; refusing to guess" >&2
        exit 2
    fi
    AICC_CRYPTO_BACKEND=""
    echo "WARNING: --no-encrypt — this archive is readable by anyone who can read the file." >&2
    echo "WARNING: SRV-08 requires encrypted backups; do not use this for anything retained." >&2
else
    set +e
    crypto_resolve_encrypt_backend
    resolve_status=$?
    set -e
    if (( resolve_status == 1 )); then
        echo "no encryption recipient given." >&2
        echo "pass --age-recipient/--age-recipients-file or --gpg-recipient," >&2
        echo "or --no-encrypt if you genuinely want a plaintext dump." >&2
        exit 2
    elif (( resolve_status != 0 )); then
        exit "$resolve_status"
    fi
fi

: "${AICC_PG_HOST:?AICC_PG_HOST is required}"
: "${AICC_PG_DB:?AICC_PG_DB is required}"
: "${AICC_PG_USER:?AICC_PG_USER is required}"
: "${AICC_PG_PASSWORD:?AICC_PG_PASSWORD is required}"
PGPORT_VALUE="${AICC_PG_PORT:-5432}"

command -v pg_dump >/dev/null || { echo "pg_dump not found in PATH" >&2; exit 127; }

# Backups routinely contain every row in the system, so a directory this script
# creates is owner-only. An existing directory is left alone: it may be an
# operator-managed shared location whose permissions are a deliberate choice.
if [[ ! -d "$OUT_DIR" ]]; then
    mkdir -p "$OUT_DIR"
    chmod 700 "$OUT_DIR"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SUFFIX="$(crypto_suffix)"
ARCHIVE="${OUT_DIR}/aicc-${AICC_PG_DB}-${STAMP}.dump${SUFFIX}"
TMP_ARCHIVE="${ARCHIVE}.partial"

# The fifo carries a second copy of the dump stream to sha256sum. It lives in a
# private directory rather than beside the archive so that nothing under
# --out-dir is ever a name a collector might mistake for a backup.
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aicc-backup.XXXXXX")"
chmod 700 "$WORK_DIR"

cleanup() { rm -f "$TMP_ARCHIVE"; rm -rf "$WORK_DIR"; }
trap cleanup EXIT

if [[ -n "$AICC_CRYPTO_BACKEND" ]]; then
    echo "backing up ${AICC_PG_USER}@${AICC_PG_HOST}:${PGPORT_VALUE}/${AICC_PG_DB}" \
         "-> ${ARCHIVE} (encrypted with ${AICC_CRYPTO_BACKEND})"
else
    echo "backing up ${AICC_PG_USER}@${AICC_PG_HOST}:${PGPORT_VALUE}/${AICC_PG_DB} -> ${ARCHIVE}"
fi

PLAIN_DIGEST_FILE="${WORK_DIR}/plain.sha256"
PLAIN_FIFO="${WORK_DIR}/plain.fifo"
mkfifo "$PLAIN_FIFO"

# Started before the writer, because `tee` blocks opening the fifo until a
# reader is attached. sha256sum consumes to EOF, so it cannot exit early and
# SIGPIPE the dump out from under us.
sha256_of_stdin < "$PLAIN_FIFO" > "$PLAIN_DIGEST_FILE" &
DIGEST_PID=$!

# pg_dump writes to stdout, so the plaintext exists only as bytes in a pipe:
# there is no window in which a readable dump is sitting in the filesystem, and
# no shred-the-temp-file step that has to be got right. The archive is written
# to a .partial name and renamed only on success, so a crashed or out-of-disk
# run cannot leave a half-written file that looks like a backup.
#
# `set -o pipefail` is what makes this safe: a pg_dump or age failure anywhere
# in the pipeline fails the script instead of producing a short archive.
if [[ -n "$AICC_CRYPTO_BACKEND" ]]; then
    PGPASSWORD="$AICC_PG_PASSWORD" pg_dump \
        --host="$AICC_PG_HOST" \
        --port="$PGPORT_VALUE" \
        --username="$AICC_PG_USER" \
        --dbname="$AICC_PG_DB" \
        --format=custom \
        --compress=9 \
        --no-owner \
        --no-privileges \
        | tee "$PLAIN_FIFO" \
        | crypto_encrypt_to "$TMP_ARCHIVE"
else
    PGPASSWORD="$AICC_PG_PASSWORD" pg_dump \
        --host="$AICC_PG_HOST" \
        --port="$PGPORT_VALUE" \
        --username="$AICC_PG_USER" \
        --dbname="$AICC_PG_DB" \
        --format=custom \
        --compress=9 \
        --no-owner \
        --no-privileges \
        | tee "$PLAIN_FIFO" \
        > "$TMP_ARCHIVE"
fi

wait "$DIGEST_PID"
PLAIN_SHA256="$(cat "$PLAIN_DIGEST_FILE")"

# A digest reader that died after `wait` reported success would leave a short
# or empty file here, and a .meta claiming an impossible plaintext digest is
# worse than none: it fails every future restore for the wrong reason.
[[ "$PLAIN_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "internal error: plaintext digest is not a SHA-256 ('${PLAIN_SHA256}')" >&2
    exit 1
}

mv "$TMP_ARCHIVE" "$ARCHIVE"
trap 'rm -rf "$WORK_DIR"' EXIT
chmod 600 "$ARCHIVE"

BASE="$(basename "$ARCHIVE")"
CIPHER_SHA256="$(sha256_of "$ARCHIVE")"

# sha256sum's own format, so `sha256sum --check` works on it unmodified — at
# the destination, in a recovery shell, by hand.
printf '%s  %s\n' "$CIPHER_SHA256" "$BASE" > "${ARCHIVE}.sha256"
chmod 600 "${ARCHIVE}.sha256"

cat > "${ARCHIVE}.meta" <<META
archive=${BASE}
database=${AICC_PG_DB}
created_at=${STAMP}
encryption=${AICC_CRYPTO_BACKEND:-none}
format=pg_dump-custom
cipher_sha256=${CIPHER_SHA256}
plain_sha256=${PLAIN_SHA256}
META
chmod 600 "${ARCHIVE}.meta"

if [[ "$VERIFY" == "1" ]]; then
    if [[ -z "$AICC_CRYPTO_BACKEND" ]]; then
        echo "verifying archive is readable"
        pg_restore --list "$ARCHIVE" > /dev/null
    else
        # Everything this host can honestly check: that the file is a
        # well-formed container for the backend we asked for, and that reading
        # it back yields the bytes we hashed. Proving the archive *restores*
        # needs the identity, which by design is not here.
        echo "verifying ciphertext container and checksum"
        detected="$(crypto_detect_file "$ARCHIVE")"
        if [[ "$detected" != "$AICC_CRYPTO_BACKEND" ]]; then
            echo "expected a ${AICC_CRYPTO_BACKEND} archive but wrote a '${detected}' one" >&2
            exit 1
        fi
        if [[ "$(sha256_of "$ARCHIVE")" != "$CIPHER_SHA256" ]]; then
            echo "archive changed between writing and re-reading it" >&2
            exit 1
        fi
        echo "container ok — run scripts/aicc_pg_verify_backup.sh where the identity lives"
        echo "for the restore-readability check this host cannot perform"
    fi
fi

if [[ -n "$OFFHOST_DEST" ]]; then
    echo "copying off-host -> ${OFFHOST_DEST}"
    for sidecar in "$ARCHIVE" "${ARCHIVE}.sha256" "${ARCHIVE}.meta"; do
        offhost_put "$sidecar" "$OFFHOST_DEST"
    done

    # The integrity check that matters: the digest is computed by the
    # destination over the bytes that landed there, not asserted by the sender
    # because its transfer command exited 0.
    echo "verifying off-host copy"
    remote_sha="$(offhost_digest "${OFFHOST_DEST%/}/${BASE}")"
    if [[ "$remote_sha" != "$CIPHER_SHA256" ]]; then
        echo "off-host copy does not match: ${remote_sha} != ${CIPHER_SHA256}" >&2
        exit 5
    fi
    for name in "${BASE}.sha256" "${BASE}.meta"; do
        if ! offhost_exists "${OFFHOST_DEST%/}/${name}"; then
            echo "off-host copy is missing ${name}" >&2
            exit 5
        fi
    done
    echo "off-host copy verified: ${OFFHOST_DEST%/}/${BASE}"
else
    echo "NOTE: no --offhost-dest; this archive exists only on the database host."
fi

if [[ -n "$KEEP" ]]; then
    echo "pruning all but the newest ${KEEP} local archives"
    # shellcheck disable=SC2012 — names are generated above and contain no spaces.
    ls -1t "${OUT_DIR}"/aicc-"${AICC_PG_DB}"-*.dump \
           "${OUT_DIR}"/aicc-"${AICC_PG_DB}"-*.dump.age \
           "${OUT_DIR}"/aicc-"${AICC_PG_DB}"-*.dump.gpg 2>/dev/null \
        | tail -n "+$((KEEP + 1))" \
        | while read -r stale; do
            rm -f "$stale" "${stale}.sha256" "${stale}.meta"
            echo "  removed $(basename "$stale")"
        done
fi

echo "backup complete: ${ARCHIVE}"
echo "checksum:        ${ARCHIVE}.sha256"
echo "metadata:        ${ARCHIVE}.meta"
