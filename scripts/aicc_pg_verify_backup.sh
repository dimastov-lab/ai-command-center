#!/usr/bin/env bash
# Verify an encrypted AICC backup, without a database (VOYN-W0-AICC-SRV-08).
#
# Runs where the *identity* lives — the off-host key holder — which is the only
# place the full chain can be checked, and deliberately not the database host.
# Four questions, in the order that makes a failure legible:
#
#   1. Did the bytes survive the copy?   ciphertext SHA-256 vs the .sha256 sidecar
#   2. Can we still decrypt them?        age/gpg with this host's identity
#   3. Is it the dump we took?           plaintext SHA-256 vs the .meta sidecar
#   4. Would it restore?                 pg_restore --list over the plaintext
#
# Step 3 is the one a checksum-only check misses: a ciphertext can be intact and
# decryptable and still not be the archive whose digest the backup host
# recorded, because encryption to a public key authenticates nothing about who
# produced the file.
#
# The decrypted dump is written to a mode-700 temporary directory and removed on
# exit, including on failure. Point --work-dir at a tmpfs if the plaintext must
# not touch a disk at all.
#
# Usage:
#   scripts/aicc_pg_verify_backup.sh \
#       --archive backups@vault.internal:/srv/aicc/aicc-aicc-20260813T020000Z.dump.age \
#       --age-identity ~/.config/aicc/backup-identity.txt
#
#   --archive accepts a local path or `[user@]host:/path` (fetched over ssh).
#   gpg archives need no identity flag; the secret key comes from the keyring.

set -euo pipefail

# shellcheck source=scripts/lib/aicc_backup_lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/aicc_backup_lib.sh"

ARCHIVE=""
WORK_DIR_OPT=""

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --archive)      ARCHIVE="${2:?--archive needs a path}"; shift 2 ;;
        --age-identity) crypto_add_age_identity "${2:?--age-identity needs a path}"; shift 2 ;;
        --work-dir)     WORK_DIR_OPT="${2:?--work-dir needs a path}"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

[[ -n "$ARCHIVE" ]] || { echo "--archive is required" >&2; exit 2; }
command -v pg_restore >/dev/null || { echo "pg_restore not found in PATH" >&2; exit 127; }

WORK_DIR="$(mktemp -d "${WORK_DIR_OPT:-${TMPDIR:-/tmp}}/aicc-verify.XXXXXX")"
chmod 700 "$WORK_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

# --------------------------------------------------------------------------
# 1. Fetch (if off-host) and check the ciphertext digest
# --------------------------------------------------------------------------

BASE="$(basename "$ARCHIVE")"
LOCAL_ARCHIVE="${WORK_DIR}/${BASE}"

if offhost_is_remote "$ARCHIVE"; then
    echo "fetching ${ARCHIVE}"
else
    [[ -f "$ARCHIVE" ]] || { echo "archive not found: ${ARCHIVE}" >&2; exit 2; }
fi
offhost_get "$ARCHIVE" "$LOCAL_ARCHIVE"

for sidecar in sha256 meta; do
    if offhost_exists "${ARCHIVE}.${sidecar}"; then
        offhost_get "${ARCHIVE}.${sidecar}" "${LOCAL_ARCHIVE}.${sidecar}"
    fi
done

CIPHER_SHA256="$(sha256_of "$LOCAL_ARCHIVE")"

if [[ -f "${LOCAL_ARCHIVE}.sha256" ]]; then
    expected="$(awk '{print $1}' < "${LOCAL_ARCHIVE}.sha256")"
    if [[ "$expected" != "$CIPHER_SHA256" ]]; then
        echo "ciphertext checksum mismatch" >&2
        echo "  recorded: ${expected}" >&2
        echo "  actual:   ${CIPHER_SHA256}" >&2
        exit 4
    fi
    echo "ciphertext checksum ok"
else
    echo "WARNING: no .sha256 sidecar; transport integrity not verified" >&2
fi

# --------------------------------------------------------------------------
# 2. Decrypt
# --------------------------------------------------------------------------

DETECTED="$(crypto_detect_file "$LOCAL_ARCHIVE")"
PLAINTEXT="${WORK_DIR}/plain.dump"

if [[ "$DETECTED" == "plain" ]]; then
    # Not a failure — the archive may predate SRV-08, or have been taken with
    # --no-encrypt — but it is a finding, because the point of this script is
    # that the file it verifies is unreadable to whoever holds it.
    echo "WARNING: ${BASE} is NOT encrypted; anyone holding this file can read it" >&2
    PLAINTEXT="$LOCAL_ARCHIVE"
else
    AICC_CRYPTO_BACKEND="$DETECTED"
    echo "decrypting with ${DETECTED}"
    crypto_decrypt_to "$LOCAL_ARCHIVE" "$PLAINTEXT"
    chmod 600 "$PLAINTEXT"
fi

# --------------------------------------------------------------------------
# 3. Is it the dump the backup host took?
# --------------------------------------------------------------------------

PLAIN_SHA256="$(sha256_of "$PLAINTEXT")"

if [[ -f "${LOCAL_ARCHIVE}.meta" ]]; then
    recorded="$(meta_read "${LOCAL_ARCHIVE}.meta" plain_sha256 || true)"
    if [[ -z "$recorded" ]]; then
        echo "WARNING: .meta has no plain_sha256; end-to-end integrity not verified" >&2
    elif [[ "$recorded" != "$PLAIN_SHA256" ]]; then
        echo "decrypted dump does not match the digest recorded at backup time" >&2
        echo "  recorded: ${recorded}" >&2
        echo "  actual:   ${PLAIN_SHA256}" >&2
        exit 4
    else
        echo "plaintext digest matches the backup host's record"
    fi
else
    echo "WARNING: no .meta sidecar; cannot confirm this is the dump that was taken" >&2
fi

# --------------------------------------------------------------------------
# 4. Would it restore?
# --------------------------------------------------------------------------

echo "listing archive contents"
TOC_LINES="$(pg_restore --list "$PLAINTEXT" | grep -c ';' || true)"
if [[ "${TOC_LINES:-0}" -lt 1 ]]; then
    echo "archive has an empty table of contents — it would restore nothing" >&2
    exit 4
fi

echo "verified: ${BASE}"
echo "  encryption:     ${DETECTED}"
echo "  cipher sha256:  ${CIPHER_SHA256}"
echo "  plain sha256:   ${PLAIN_SHA256}"
echo "  toc entries:    ${TOC_LINES}"
