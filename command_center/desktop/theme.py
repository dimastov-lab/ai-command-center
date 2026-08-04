"""Theme mode, palette resolution, and application-wide stylesheet generation.

Implements `DESIGN_SYSTEM.md` §2: three theme modes (Light / Dark / System). The
System mode follows the OS appearance live via Qt's ``QStyleHints.colorScheme()``
and its ``colorSchemeChanged`` signal — a pure-Qt mechanism that needs no OS
branching, so it introduces no ``sys.platform`` check outside the (future)
``command_center.platform`` package (`ARCHITECTURE.md` §6/§8.1).

The stylesheet is generated once per effective palette from the tokens in
:mod:`command_center.desktop.tokens`, so a component never hardcodes a colour.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication

from command_center.platform.preferences import DensityMode, ThemeMode

from . import tokens
from .tokens import Palette


def resolve_palette(mode: ThemeMode, os_scheme: Qt.ColorScheme) -> Palette:
    """Map a (mode, OS scheme) pair to the concrete palette to apply.

    ``System`` follows ``os_scheme``; an ``Unknown`` OS scheme (e.g. the
    offscreen platform, or an OS that does not report one) resolves to Light,
    the documented fresh-install-safe default.
    """
    if mode is ThemeMode.LIGHT:
        return tokens.LIGHT
    if mode is ThemeMode.DARK:
        return tokens.DARK
    # System
    if os_scheme is Qt.ColorScheme.Dark:
        return tokens.DARK
    return tokens.LIGHT


def build_stylesheet(
    p: Palette, density: DensityMode = DensityMode.COMFORTABLE
) -> str:
    """Return the application-wide Qt stylesheet for palette ``p``.

    Object names referenced here (``#AppShell``, ``#Sidebar`` …) are set by the
    corresponding widgets so the rules target them without leaking colours into
    the widgets' own code.
    """
    control_height = (
        tokens.CONTROL_HEIGHT_MD
        if density is DensityMode.COMPACT
        else tokens.CONTROL_HEIGHT_LG
    )
    control_padding = (
        tokens.SPACE_XS if density is DensityMode.COMPACT else tokens.SPACE_SM
    )
    button_content_height = (
        control_height - (2 * control_padding) - (2 * tokens.BORDER_HAIRLINE_PX)
    )
    combo_content_height = (
        control_height - (2 * tokens.SPACE_XS) - (2 * tokens.BORDER_HAIRLINE_PX)
    )
    return f"""
    QWidget {{
        color: {p.text_primary};
        background-color: {p.bg_base};
        font-size: {tokens.TYPE_BODY_PT}pt;
    }}
    QMainWindow, #AppShell {{ background-color: {p.bg_base}; }}

    #Sidebar {{
        background-color: {p.sidebar_bg};
        border-right: {tokens.BORDER_HAIRLINE_PX}px solid {p.border};
    }}
    #SidebarTitle {{
        color: {p.text_secondary};
        font-size: {tokens.TYPE_CAPTION_PT}pt;
        font-weight: 600;
        padding: {tokens.SPACE_SM}px;
    }}

    #TopBar {{
        background-color: {p.topbar_bg};
        border-bottom: {tokens.BORDER_HAIRLINE_PX}px solid {p.border};
    }}
    #StatusArea {{ color: {p.text_secondary}; font-size: {tokens.TYPE_CAPTION_PT}pt; }}

    QPushButton, QToolButton {{
        background-color: {p.surface};
        border: {tokens.BORDER_HAIRLINE_PX}px solid {p.border};
        border-radius: {tokens.RADIUS_MD}px;
        min-height: {button_content_height}px;
        padding: {control_padding}px {tokens.SPACE_MD}px;
        color: {p.text_primary};
    }}
    QPushButton:hover:enabled, QToolButton:hover:enabled {{ background-color: {p.hover_bg}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {p.text_disabled}; }}
    QPushButton:focus, QToolButton:focus, QComboBox:focus {{
        border: {tokens.BORDER_FOCUS_PX}px solid {p.accent};
    }}
    QComboBox {{
        background-color: {p.surface};
        border: {tokens.BORDER_HAIRLINE_PX}px solid {p.border};
        border-radius: {tokens.RADIUS_MD}px;
        padding: {tokens.SPACE_XS}px {tokens.SPACE_SM}px;
        color: {p.text_primary};
        min-height: {combo_content_height}px;
    }}
    QComboBox:disabled {{ color: {p.text_disabled}; }}

    /* NavigationItem is a QPushButton subclass; these rules come *after* the
       generic QPushButton rules above so the equal-specificity tie resolves
       here — the sidebar item stays flat, not boxed like a real button. */
    NavigationItem {{
        background-color: transparent;
        border: none;
        border-radius: {tokens.RADIUS_MD}px;
        padding-left: {tokens.SPACE_MD}px;
        padding-right: {tokens.SPACE_MD}px;
        text-align: left;
        color: {p.text_primary};
    }}
    NavigationItem:hover:enabled {{ background-color: {p.hover_bg}; }}
    NavigationItem:checked {{
        background-color: {p.selected_bg};
        color: {p.accent_emphasis};
        border: {tokens.BORDER_HAIRLINE_PX}px solid {p.accent};
        font-weight: 600;
    }}
    NavigationItem:disabled {{ color: {p.text_disabled}; background-color: transparent; }}
    NavigationItem:focus {{
        outline: none;
        border: {tokens.BORDER_FOCUS_PX}px solid {p.accent};
    }}

    #PageTitle {{ font-size: {tokens.TYPE_DISPLAY_PT}pt; font-weight: 600; }}
    #PageDescription {{ color: {p.text_secondary}; font-size: {tokens.TYPE_BODY_PT}pt; }}

    #EmptyStateTitle {{ font-size: {tokens.TYPE_TITLE_PT}pt; font-weight: 600; }}
    #EmptyStateBody {{ color: {p.text_secondary}; }}

    QRadioButton {{
        color: {p.text_primary};
        spacing: {tokens.SPACE_SM}px;
    }}
    QRadioButton::indicator {{
        width: {tokens.ICON_SM}px;
        height: {tokens.ICON_SM}px;
        border: {tokens.BORDER_FOCUS_PX}px solid {p.text_secondary};
        border-radius: {tokens.SPACE_SM}px;
        background-color: {p.surface};
    }}
    QRadioButton::indicator:hover {{
        border-color: {p.accent_emphasis};
    }}
    QRadioButton::indicator:checked {{
        border: {tokens.BORDER_FOCUS_PX}px solid {p.accent_emphasis};
        background-color: {p.accent};
    }}
    QRadioButton:focus {{
        color: {p.accent_emphasis};
    }}

    /* Labels never paint their own background, so a card's surface shows through
       cleanly (§1.7 border-forward elevation). */
    QLabel {{ background-color: transparent; }}

    /* Workspace Home (D2C) — cards, metric tiles, list rows, section headers. */
    #Card, #MetricCard {{
        background-color: {p.surface};
        border: {tokens.BORDER_HAIRLINE_PX}px solid {p.border};
        border-radius: {tokens.RADIUS_MD}px;
    }}
    #CardTitle {{ font-size: {tokens.TYPE_TITLE_PT}pt; font-weight: 600; }}
    #CardStat, #RowMeta {{ color: {p.text_secondary}; font-size: {tokens.TYPE_CAPTION_PT}pt; }}
    #MetricValue {{ font-size: {tokens.TYPE_DISPLAY_PT}pt; font-weight: 600; }}
    #MetricCaption {{ color: {p.text_secondary}; font-size: {tokens.TYPE_CAPTION_PT}pt; }}
    #SectionTitle {{ font-size: {tokens.TYPE_TITLE_PT}pt; font-weight: 600; }}
    #RunSummary, #ActivityItem, #ArtifactRow, #ReportRow, #WorktreeRow {{
        border-bottom: {tokens.BORDER_HAIRLINE_PX}px solid {p.border};
    }}
    """


class ThemeController(QObject):
    """Applies a :class:`ThemeMode` to a ``QApplication`` and re-applies it live
    when the OS scheme changes while in System mode.

    Owns no persistence — the current mode is handed to it (and read from it) by
    the shell, which persists it through :class:`command_center.desktop.settings.SettingsStore`.
    """

    palette_changed = Signal(object)  # emits the newly-applied Palette

    def __init__(
        self,
        app: QGuiApplication,
        mode: ThemeMode = ThemeMode.SYSTEM,
        density: DensityMode = DensityMode.COMFORTABLE,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._mode = mode
        self._density = density
        self._palette: Palette | None = None
        app.styleHints().colorSchemeChanged.connect(self._on_os_scheme_changed)

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def density(self) -> DensityMode:
        return self._density

    @property
    def palette(self) -> Palette | None:
        """The palette currently applied (None before the first :meth:`apply`)."""
        return self._palette

    def set_mode(self, mode: ThemeMode) -> None:
        self._mode = mode
        self.apply()

    def set_density(self, density: DensityMode) -> None:
        self._density = density
        self.apply()

    def apply(self) -> None:
        """Resolve the effective palette for the current mode + OS scheme and
        install it as the application stylesheet."""
        os_scheme = self._app.styleHints().colorScheme()
        palette = resolve_palette(self._mode, os_scheme)
        self._palette = palette
        self._app.setStyleSheet(build_stylesheet(palette, self._density))
        self.palette_changed.emit(palette)

    def _on_os_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        # Only System mode tracks the OS; Light/Dark are pinned.
        if self._mode is ThemeMode.SYSTEM:
            self.apply()
