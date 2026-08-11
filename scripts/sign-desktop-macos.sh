#!/usr/bin/env bash
# Подпись, notarization и staple macOS-сборки (#197, SIGNING_RUNBOOK.md §2).
# Запускается ПОСЛЕ scripts/build-desktop-macos.sh на машине владельца,
# где в Keychain есть Developer ID Application и профиль notarytool.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app="$repo_root/dist/macos/AI Command Center.app"
dmg="$repo_root/dist/macos/AICommandCenter.dmg"
entitlements="$repo_root/packaging/macos/entitlements.plist"
notary_profile="${AICC_NOTARY_PROFILE:-aicc-notary}"

if [[ ! -d "$app" ]]; then
  echo "Нет сборки: $app — сначала scripts/build-desktop-macos.sh" >&2
  exit 2
fi

identity="${AICC_SIGN_IDENTITY:-}"
if [[ -z "$identity" ]]; then
  identity="$(security find-identity -v -p codesigning \
    | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)"
fi
if [[ -z "$identity" ]]; then
  echo "Нет Developer ID Application identity в Keychain." >&2
  echo "Owner-шаги: docs/desktop/SIGNING_RUNBOOK.md §1 (macOS, пп. 1–4)." >&2
  exit 2
fi
if ! xcrun notarytool history --keychain-profile "$notary_profile" >/dev/null 2>&1; then
  echo "Нет notarytool-профиля '$notary_profile' (SIGNING_RUNBOOK.md §1 п. 4)." >&2
  exit 2
fi

echo "Identity: $identity"

# Вложенные Mach-O подписываются до бандла (inside-out): --deep deprecated
# и пропускает часть вложений PyInstaller.
find "$app/Contents" \( -name '*.dylib' -o -name '*.so' \) -type f -print0 \
  | xargs -0 -I{} codesign --force --options runtime --timestamp \
      --sign "$identity" "{}"
codesign --force --options runtime --timestamp \
  --entitlements "$entitlements" --sign "$identity" "$app"
codesign --verify --deep --strict "$app"

hdiutil create -volname "AI Command Center" -srcfolder "$app" \
  -ov -format UDZO "$dmg"
xcrun notarytool submit "$dmg" --keychain-profile "$notary_profile" --wait
xcrun stapler staple "$dmg"

spctl -a -t open --context context:primary-signature -v "$dmg"
echo "Подписано и notarized: $dmg"
