# AI Command Center — Desktop Platform Behavior

Status: **D0 — target platform contract.** `command_center.platform` (referenced throughout) is
target D3 structure (`DESKTOP_INCREMENT_1.md`) — it does not exist in the repository yet. This
document defines macOS and Windows 11 behavior and the abstraction contract every other package
must go through to reach it (binding decisions 6, 8).

## 1. macOS (Apple Silicon)

macOS Apple Silicon is a first target platform (binding decision 6).

- **Packaging format**: `.app` bundle. A development DMG (unsigned) is the distribution artifact
  for D4 smoke testing — see `DESKTOP_INCREMENT_1.md` D4.
- **Finder reveal**: `command_center.platform.reveal_in_file_manager(path)` opens Finder with
  the given path selected (`osascript`/`NSWorkspace`-equivalent, via whatever PySide6-compatible
  mechanism is selected at D3 implementation time — `QDesktopServices` or a native call).
- **Native menu behavior**: a standard macOS menu bar (App menu with About/Preferences/Quit,
  Window menu) — not a custom in-window menu bar. "Preferences" (⌘,) opens Settings, matching
  every other native macOS application's convention.
- **Dock behavior**: standard Dock icon presence while running; the application does not
  register itself as a background-only (agent) app in Desktop Increment 1 — it always shows a
  Dock icon and a menu bar while open, exactly like a normal foreground application.
- **System appearance**: the application follows macOS's Light/Dark/Auto appearance setting when
  the theme preference is set to "System" (§2 of `DESIGN_SYSTEM.md`), updating live when the user
  changes it in System Settings while the app is running.
- **Settings storage**: `QSettings` with the macOS native backend (`NSUserDefaults`-backed),
  scoped by organization/application identifier set once at `QApplication` construction.
- **Gatekeeper expectations for unsigned development builds**: a development `.app`/DMG is
  unsigned and unnotarized in Desktop Increment 1. Opening it will trigger Gatekeeper's "cannot
  be opened because it is from an unidentified developer" warning; the documented workaround for
  internal testing is right-click → Open (or `xattr -d com.apple.quarantine` on the bundle) — this
  is expected, not a bug, and is not silently worked around by the build itself.
- **No signing/notarization in Increment 1** — explicitly out of scope (binding decision 12);
  see `DESKTOP_INCREMENT_1.md` D4 for what D4 packaging does and does not include.

## 2. Windows 11 (x64)

Windows 11 x64 is a first target platform (binding decision 6).

- **Packaging format**: `.exe`. A development installer or a packaged folder (both acceptable for
  D4 — see `DESKTOP_INCREMENT_1.md` D4) is the distribution artifact for smoke testing.
- **Explorer reveal**: `command_center.platform.reveal_in_file_manager(path)` opens Explorer with
  the given path selected (`explorer.exe /select,<path>` or the PySide6-compatible equivalent
  chosen at D3 implementation time).
- **Start Menu/taskbar behavior**: standard Start Menu entry (for an installer-based build) and
  standard taskbar icon/window behavior while running — no custom taskbar integration beyond
  what Qt provides by default.
- **System appearance**: the application follows Windows 11's "Choose your mode" (Light/Dark)
  setting when the theme preference is "System," updating live when the user changes it while the
  app is running.
- **Settings storage**: `QSettings` with the Windows native backend (registry-backed), scoped by
  organization/application name set once at `QApplication` construction.
- **SmartScreen expectations for unsigned development builds**: an unsigned development `.exe`/
  installer will trigger Windows SmartScreen's "Windows protected your PC" warning on first run;
  the documented workaround for internal testing is "More info" → "Run anyway" — this is
  expected, not a bug, and is not silently worked around by the build itself.
- **No production signing in Increment 1** — explicitly out of scope (binding decision 12); see
  `DESKTOP_INCREMENT_1.md` D4.

## 3. Platform abstraction contract

`command_center.platform` is the **only** package permitted to branch on OS
(`ARCHITECTURE.md` §6, §8.1). Its public contract, implemented once per platform behind a single
interface:

| Function | Purpose | macOS behavior | Windows behavior |
|---|---|---|---|
| `reveal_in_file_manager(path: Path) -> None` | Open the platform file browser with `path` selected | Finder, path selected | Explorer, path selected |
| `platform_name() -> str` | Return a stable platform identifier (`"macos"` / `"windows"`) for any display or branching that must happen above this layer (there should be very little — see `ARCHITECTURE.md` §8.1) | `"macos"` | `"windows"` |
| `system_theme() -> Literal["light", "dark"]` + a change-notification mechanism | Resolve the current OS appearance and notify `command_center.desktop` when it changes, for "System" theme mode (§2 of `DESIGN_SYSTEM.md`) | Reads macOS's Appearance setting | Reads Windows 11's "Choose your mode" setting |
| `settings_path() -> Path` (or `QSettings` handle) | Where window/theme/workspace preferences persist | `NSUserDefaults`-backed `QSettings` | Registry-backed `QSettings` |
| `log_dir() -> Path` | Standard per-platform log directory | `~/Library/Logs/<app>` | `%LOCALAPPDATA%\<app>\Logs` |
| `cache_dir() -> Path` | Standard per-platform cache directory | `~/Library/Caches/<app>` | `%LOCALAPPDATA%\<app>\Cache` |
| `crash_dir() -> Path` | Standard per-platform crash-report directory (if the packaging tool produces one at D4) | `~/Library/Application Support/<app>/CrashReports` (or the packaging tool's default) | `%LOCALAPPDATA%\<app>\CrashReports` (or the packaging tool's default) |

Exact directory names/identifiers (the `<app>` segment, organization identifier) are fixed once at
D1A implementation time (`IMPLEMENTATION_ROADMAP.md`) and reused consistently across every path
above — this document fixes the contract's shape and each platform's standard-location
convention, not the final literal string.

## 4. No platform branching outside this contract

No `command_center.runtime`, `command_center.application`, or existing `command_center/*` domain
module may contain platform-specific branching (`sys.platform`, `platform.system()`,
hardcoded `/Users/...` or `C:\...` paths, or an `if macos else windows` conditional of any kind).
Every one of those modules is already platform-agnostic today (they operate on `Path` objects and
subprocess calls that behave identically on both target platforms) — this rule preserves that
property going forward rather than introducing a new constraint the existing code would need to
be refactored to satisfy. Any genuinely new platform-specific need discovered during
implementation is added to the `command_center.platform` contract in this document, not
special-cased inline elsewhere.
