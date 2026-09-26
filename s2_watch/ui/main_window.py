import sqlite3
from html import escape
from pathlib import Path
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QMetaObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem,
    QFileDialog, QSystemTrayIcon, QTabWidget, QVBoxLayout,
    QWidget, QMenu, QToolButton, QWidgetAction, QAbstractItemView, QInputDialog,
)

from s2_watch import __version__ as APP_VERSION


class SortableTableItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left_timestamp = self.data(Qt.ItemDataRole.UserRole)
        right_timestamp = other.data(Qt.ItemDataRole.UserRole)
        if left_timestamp and right_timestamp:
            return str(left_timestamp) < str(right_timestamp)
        return super().__lt__(other)


from s2_watch.adapters.js8call_tcp import JS8CallTcpAdapter
from s2_watch.adapters.magcommstat_sqlite import MagCommstatSQLiteAdapter
from s2_watch.adapters.commstat_sqlite import CommstatSQLiteAdapter
from s2_watch.adapters.flmsg_files import FLMSGFileAdapter
from s2_watch.adapters.js8superspotter_sqlite import JS8SuperSpotterSQLiteAdapter
from s2_watch.analysis.base import AnalysisResult
from s2_watch.analysis.base import NewsAnalyzer
from s2_watch.analysis.ollama import FallbackNewsAnalyzer, OllamaNewsAnalyzer
from s2_watch.analysis.rules import RuleBasedNewsAnalyzer
from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.models import ANY_APPLICATION, Message, WatchForm
from s2_watch.analysis.category_patterns import CATEGORY_PATTERNS
from s2_watch.forms_io import forms_from_json, forms_to_json
from s2_watch.monitor_worker import MonitorConfig, MonitorWorker, ProcessedMessage
from s2_watch.storage import SQLiteStore
from s2_watch.ui.icons import app_icon
from s2_watch.ui.map_view import MapView
from s2_watch.ui.metrics_view import MetricsView
from s2_watch.overlays import NwsAlertsPoller
from s2_watch.ui.watch_form_dialog import WatchFormDialog


class MainWindow(QMainWindow):
    worker_config = Signal(object)  # MonitorConfig, delivered to the polling thread
    # On-screen tables keep only the newest rows; everything stays searchable in History.
    LIVE_ROWS_LIMIT = 2000
    ANALYSIS_ROWS_LIMIT = 1000
    ANALYSIS_COLUMNS = (
        "Time", "Application", "Watch form", "Result", "Status", "Category", "Urgency",
        "Confidence", "Seen", "Summary", "Message",
    )
    SEEN_COLUMN = ANALYSIS_COLUMNS.index("Seen")
    STATUS_COLUMN = ANALYSIS_COLUMNS.index("Status")
    # Report triage: (status, label with icon so it never relies on colour, shortcut).
    STATUS_ACTIONS = (
        ("interest", "★ Of interest", "I"),
        ("followup", "↪ Follow-up created", "F"),
        ("ignored", "⊘ Ignored", "X"),
        ("", "Clear label", "C"),
    )
    STATUS_LABELS = {status: label for status, label, _key in STATUS_ACTIONS if status}
    REPORT_ROLE = Qt.ItemDataRole.UserRole + 2
    DEFAULT_WATCH_FORMS = {
        "JS8Call": "All JS8Call messages",
        "MagCommstat": "All MagCommstat messages",
        "Commstat": "All Commstat messages",
        "FLMSG": "All FLMSG messages",
        "JS8SuperSpotter": "All JS8SuperSpotter messages",
    }

    def __init__(self, database_path: str = "s2_watch.db") -> None:
        super().__init__()
        self.setWindowTitle(f"S2 Watch {APP_VERSION}")
        self.resize(800, 500)
        self.store = SQLiteStore(database_path)
        self.host = QLineEdit(self.store.get_setting("js8call.host", "127.0.0.1"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        try:
            saved_port = int(self.store.get_setting("js8call.port", "2442"))
        except ValueError:
            saved_port = 2442
        self.port.setValue(saved_port if 1 <= saved_port <= 65535 else 2442)
        self.review_duration = QComboBox()
        review_options = (
            ("6 hours", 6), ("12 hours", 12), ("24 hours", 24),
            ("2 days", 48), ("5 days", 120), ("1 week", 168),
            ("2 weeks", 336), ("1 month", 720),
        )
        for label, hours in review_options:
            self.review_duration.addItem(label, hours)
        try:
            saved_hours = int(self.store.get_setting("review.hours", "24"))
        except ValueError:
            saved_hours = 24
        index = self.review_duration.findData(saved_hours)
        self.review_duration.setCurrentIndex(index if index >= 0 else 2)
        self.review_duration.currentIndexChanged.connect(self.review_duration_changed)
        self.news_rows = QSpinBox()
        self.news_rows.setRange(1, 100)
        try:
            saved_news_rows = int(self.store.get_setting("news.rows", "10"))
        except ValueError:
            saved_news_rows = 10
        self.news_rows.setValue(min(100, max(1, saved_news_rows)))
        self.news_rows.valueChanged.connect(self.news_rows_changed)
        self.theme = self.create_choice_group(
            (("Light", "light"), ("Dark", "dark")),
            self.store.get_setting("ui.theme", "light"),
            self.theme_changed,
        )
        self.apply_theme(self.theme.checkedAction().data())
        self.time_zone = QComboBox()
        self.time_zone.addItem("Local time", "local")
        self.time_zone.addItem("Zulu (UTC)", "utc")
        saved_time_zone = self.store.get_setting("display.time_zone", "local")
        self.time_zone.setCurrentIndex(max(0, self.time_zone.findData(saved_time_zone)))
        self.time_zone.currentIndexChanged.connect(self.time_zone_changed)
        self.magcommstat_path = QLineEdit(
            self.store.get_setting(
                "magcommstat.database",
                str(Path.home() / ".local/share/MagCommstat/traffic.db3"),
            )
        )
        self.commstat_path = QLineEdit(
            self.store.get_setting(
                "commstat.database",
                str(Path.home() / "MyApps/Commstat/traffic.db3"),
            )
        )
        self.flmsg_path = QLineEdit(
            self.store.get_setting(
                "flmsg.directory",
                str(Path.home() / ".nbems/ICS/messages"),
            )
        )
        self.superspotter_path = QLineEdit(
            self.store.get_setting(
                "js8superspotter.database",
                str(Path.home() / "MyApps/JS8SuperSpotter/js8spotter.db"),
            )
        )
        connect = QPushButton("Connect to monitored applications")
        connect.clicked.connect(self.connect_js8call)
        self.status = QLabel("Disconnected")
        self.application_status_lights: dict[str, QLabel] = {}
        status_group = QVBoxLayout()
        status_group.addWidget(QLabel("Application status"))
        for application in ("JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter"):
            light = QLabel(f"● {application}: Disconnected")
            light.setStyleSheet("color: #c62828;")
            self.application_status_lights[application] = light
            status_group.addWidget(light)
        self.alerts_enabled = QCheckBox("Show desktop alerts for newsworthy messages")
        self.alerts_enabled.setChecked(self.store.get_setting("alerts.enabled", "1") == "1")
        self.alerts_enabled.stateChanged.connect(self.alert_settings_changed)
        self.alert_high_urgency = QCheckBox("Alert only for high urgency")
        self.alert_high_urgency.setChecked(
            self.store.get_setting("alerts.high_urgency_only", "0") == "1"
        )
        self.alert_high_urgency.stateChanged.connect(self.alert_settings_changed)
        self.analysis_mode = self.create_choice_group(
            (("Rules only", "rules"), ("Local Ollama with rules fallback", "ollama")),
            self.store.get_setting("analysis.mode", "rules"),
            self.analysis_mode_changed,
        )
        self.ollama_model = QLineEdit(self.store.get_setting("ollama.model", "llama3.2"))
        self.ollama_endpoint = QLineEdit(
            self.store.get_setting("ollama.endpoint", "http://127.0.0.1:11434")
        )
        self.ollama_model.editingFinished.connect(self.analysis_settings_changed)
        self.ollama_endpoint.editingFinished.connect(self.analysis_settings_changed)
        self.forms = QListWidget()
        self.forms.itemChanged.connect(self.form_changed)
        add_form = QPushButton("Add watch form")
        add_form.clicked.connect(self.add_form)
        edit_form = QPushButton("Edit selected form")
        edit_form.clicked.connect(self.edit_form)
        remove_form = QPushButton("Remove selected form")
        remove_form.clicked.connect(self.remove_form)
        import_forms = QPushButton("Import watch forms")
        import_forms.clicked.connect(self.import_forms)
        export_forms = QPushButton("Export watch forms")
        export_forms.clicked.connect(self.export_forms)
        self.messages = QTableWidget(0, 4)
        self.messages.setHorizontalHeaderLabels(
            ("Time", "Application", "Watch form", "Message")
        )
        self.messages.setWordWrap(False)
        self.messages.setAlternatingRowColors(True)
        self.messages.horizontalHeader().setStretchLastSection(True)
        self.messages.setSortingEnabled(True)
        self.messages.cellDoubleClicked.connect(self.show_message_details)
        # Newsworthiness shows one row per report; "Seen" counts its copies and sources.
        self.analysis = QTableWidget(0, len(self.ANALYSIS_COLUMNS))
        self.analysis.setHorizontalHeaderLabels(self.ANALYSIS_COLUMNS)
        self._report_items: dict[int, QTableWidgetItem] = {}
        self.analysis.setWordWrap(False)
        self.analysis.setAlternatingRowColors(True)
        self.analysis.horizontalHeader().setStretchLastSection(True)
        self.analysis.setSortingEnabled(True)
        self.analysis.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.analysis.cellClicked.connect(self.focus_analysis_message)
        self.analysis.cellDoubleClicked.connect(self.show_analysis_details)
        # Label one or many reports: select rows, then right-click or use the shortcuts.
        self.analysis.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.analysis.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.analysis.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.analysis.customContextMenuRequested.connect(self.show_report_menu)
        self.report_actions: list[QAction] = []
        for status, label, key in self.STATUS_ACTIONS:
            action = QAction(label, self.analysis)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(lambda _checked=False, status=status: self.mark_selected_reports(status))
            self.analysis.addAction(action)
            self.report_actions.append(action)
        self.edit_note_action = QAction("Edit note…", self.analysis)
        self.edit_note_action.setShortcut(QKeySequence("N"))
        self.edit_note_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.edit_note_action.triggered.connect(self.edit_selected_note)
        self.analysis.addAction(self.edit_note_action)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search message text or form name")
        self.history_category = QComboBox()
        self.history_category.addItem("Any category", "")
        self.history_category.addItems(NEWS_CATEGORIES)
        self.history_urgency = QComboBox()
        self.history_urgency.addItem("Any urgency", "")
        for urgency in ("low", "medium", "high"):
            self.history_urgency.addItem(urgency.title(), urgency)
        self.history_newsworthy = QCheckBox("Newsworthy only")
        search_history = QPushButton("Search history")
        search_history.clicked.connect(self.search_history)
        self.history = QListWidget()
        self.map_view = MapView()
        self.map_view._bridge.message_selected.connect(self.select_analysis_message)
        self.map_application_filters: dict[str, QCheckBox] = {}
        self.map_category_filters: dict[str, QCheckBox] = {}
        map_filter_layout = QVBoxLayout()
        map_filter_layout.addWidget(QLabel("Map filters"))
        self.map_filter_panel = QWidget()
        self.map_filter_panel.setLayout(map_filter_layout)
        self.map_legend_filter = QCheckBox("Show map icon legend")
        self.map_legend_filter.setChecked(
            self.store.get_setting("map.legend.visible", "1") == "1"
        )
        self.map_legend_filter.stateChanged.connect(self.map_legend_changed)
        map_filter_layout.addWidget(self.map_legend_filter)
        self.map_radar_filter = QCheckBox("Show weather radar")
        self.map_radar_filter.setChecked(
            self.store.get_setting("map.radar.visible", "0") == "1"
        )
        self.map_radar_filter.stateChanged.connect(self.map_radar_changed)
        map_filter_layout.addWidget(self.map_radar_filter)
        # Optional overlays, each remembered: (map overlay, setting key, label, tooltip).
        self.map_overlay_filters: dict[str, QCheckBox] = {}
        for overlay, label, tooltip in (
            ("nws", "Show NWS watches && warnings",
             "Active National Weather Service alerts, coloured by severity (needs internet; refreshed every 5 minutes)."),
            ("coverage", "Show station coverage",
             "Shades each grid square by when a station there was last heard, so quiet areas stand out."),
            ("greyline", "Show day / night (grey line)",
             "Shades the night side of the Earth; HF propagation changes along the grey line."),
        ):
            checkbox = QCheckBox(label)
            checkbox.setToolTip(tooltip)
            checkbox.setChecked(self.store.get_setting(f"map.{overlay}.visible", "0") == "1")
            checkbox.stateChanged.connect(lambda _state, overlay=overlay: self.map_overlay_changed(overlay))
            self.map_overlay_filters[overlay] = checkbox
            map_filter_layout.addWidget(checkbox)
        self.nws_poller = NwsAlertsPoller(parent=self)
        self.nws_poller.alerts_ready.connect(self.map_view.set_nws_alerts)
        self.nws_poller.failed.connect(self.map_view.set_nws_error)
        self.map_online_tiles = QCheckBox("Use online map tiles (OpenStreetMap)")
        self.map_online_tiles.setToolTip(
            "When off, or when there is no network, the map uses its built-in offline base map."
        )
        self.map_online_tiles.setChecked(self.store.get_setting("map.tiles.online", "1") == "1")
        self.map_online_tiles.stateChanged.connect(self.map_online_tiles_changed)
        map_filter_layout.addWidget(self.map_online_tiles)
        self.map_regions_filter = QCheckBox("Show MARS regions")
        self.map_regions_filter.setChecked(
            self.store.get_setting("map.regions.visible", "1") == "1"
        )
        self.map_regions_filter.stateChanged.connect(self.map_regions_changed)
        map_filter_layout.addWidget(self.map_regions_filter)
        map_filter_layout.addWidget(QLabel("Show location types"))
        self.map_station_filter = QCheckBox("Station locations")
        self.map_news_filter = QCheckBox("News locations")
        self.map_region_location_filter = QCheckBox("General locations (state / MARS region)")
        for checkbox in (self.map_station_filter, self.map_news_filter, self.map_region_location_filter):
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self.map_filters_changed)
            map_filter_layout.addWidget(checkbox)
        map_filter_layout.addWidget(QLabel("Show source applications"))
        for application in ("JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter"):
            checkbox = QCheckBox(application)
            checkbox.setChecked(
                self.store.get_setting(f"map.filter.application.{application}", "1") == "1"
            )
            checkbox.stateChanged.connect(self.map_filters_changed)
            self.map_application_filters[application] = checkbox
            map_filter_layout.addWidget(checkbox)
        map_filter_layout.addWidget(QLabel("Show categories"))
        for category in (*NEWS_CATEGORIES, "routine"):
            checkbox = QCheckBox(category)
            checkbox.setChecked(
                self.store.get_setting(f"map.filter.category.{category}", "1") == "1"
            )
            checkbox.stateChanged.connect(self.map_filters_changed)
            self.map_category_filters[category] = checkbox
            map_filter_layout.addWidget(checkbox)
        self.geocoding_enabled = QCheckBox("Enable place-name geocoding (sends place names to OpenStreetMap)")
        self.geocoding_enabled.setChecked(self.store.get_setting("geocoding.enabled", "0") == "1")
        self.geocoding_enabled.stateChanged.connect(self.geocoding_setting_changed)
        ollama_layout = QFormLayout()
        ollama_layout.addRow("Ollama model", self.ollama_model)
        ollama_layout.addRow("Ollama endpoint", self.ollama_endpoint)
        self.ollama_dialog = self.create_settings_dialog("Ollama Settings", ollama_layout)

        watch_forms_layout = QVBoxLayout()
        watch_forms_layout.addWidget(QLabel("Watch forms"))
        watch_forms_layout.addWidget(self.forms)
        for button in (add_form, edit_form, remove_form, import_forms, export_forms):
            watch_forms_layout.addWidget(button)
        self.watch_forms_dialog = self.create_settings_dialog("Watch Forms", watch_forms_layout)

        display_layout = QFormLayout()
        display_layout.addRow("Display time zone:", self.time_zone)
        display_layout.addRow("Newsworthiness rows shown:", self.news_rows)
        display_layout.addRow(self.alerts_enabled)
        display_layout.addRow(self.alert_high_urgency)
        display_layout.addRow(self.geocoding_enabled)
        self.display_dialog = self.create_settings_dialog("Display", display_layout)

        sources_layout = QFormLayout()
        sources_layout.addRow("JS8Call TCP host", self.host)
        sources_layout.addRow("JS8Call TCP port", self.port)
        sources_layout.addRow("MagCommstat database", self.magcommstat_path)
        sources_layout.addRow("Commstat database", self.commstat_path)
        sources_layout.addRow("FLMSG message directory", self.flmsg_path)
        sources_layout.addRow("JS8SuperSpotter database", self.superspotter_path)
        sources_layout.addRow("Review existing feeder data from:", self.review_duration)
        sources_layout.addRow(connect)
        sources_layout.addRow(status_group)
        self.sources_dialog = self.create_settings_dialog("Sources", sources_layout)

        configuration_menu = self.menuBar().addMenu("Configuration")
        analysis_menu = configuration_menu.addMenu("Analysis Mode")
        analysis_menu.addActions(self.analysis_mode.actions())
        analysis_menu.addSeparator()
        analysis_menu.addAction("Ollama settings…").triggered.connect(
            lambda: self.open_settings_dialog(self.ollama_dialog)
        )
        theme_menu = configuration_menu.addMenu("Theme")
        theme_menu.addActions(self.theme.actions())
        configuration_menu.addSeparator()
        for title, dialog in (
            ("Watch Forms…", self.watch_forms_dialog),
            ("Display…", self.display_dialog),
            ("Sources…", self.sources_dialog),
        ):
            configuration_menu.addAction(title).triggered.connect(
                lambda _checked=False, dialog=dialog: self.open_settings_dialog(dialog)
            )
        help_menu = self.menuBar().addMenu("Help")
        about_action = help_menu.addAction("About S2 Watch")
        about_action.triggered.connect(self.show_about)
        self.statusBar().addWidget(self.status, 1)

        live_layout = QVBoxLayout()
        live_layout.addWidget(QLabel("Messages matching active watch forms"))
        live_layout.addWidget(self.messages)

        history_layout = QVBoxLayout()
        history_layout.addWidget(QLabel("Search message text or form name"))
        history_layout.addWidget(self.history_search)
        history_layout.addWidget(self.history_category)
        history_layout.addWidget(self.history_urgency)
        history_layout.addWidget(self.history_newsworthy)
        history_layout.addWidget(search_history)
        history_layout.addWidget(self.history)
        self.map_filter_menu = QMenu(self)
        filter_action = QWidgetAction(self.map_filter_menu)
        filter_action.setDefaultWidget(self.map_filter_panel)
        self.map_filter_menu.addAction(filter_action)
        self.map_filter_button = QToolButton()
        self.map_filter_button.setText("Map filters")
        self.map_filter_button.setToolTip("Show or hide map filters")
        self.map_filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.map_filter_button.setMenu(self.map_filter_menu)
        map_layout = QVBoxLayout()
        map_layout.addWidget(self.map_filter_button)
        self.map_view.setMinimumHeight(500)
        map_layout.addWidget(self.map_view, 3)
        self.news_label = QLabel()
        map_layout.addWidget(self.news_label)
        self.update_news_label()
        map_layout.addWidget(self.analysis, 2)

        tabs = QTabWidget()
        for title, tab_layout in (
            ("Map", map_layout),
            ("Live Monitor", live_layout),
            ("History", history_layout),
        ):
            tab = QWidget()
            tab.setLayout(tab_layout)
            tabs.addTab(tab, title)
        self.metrics_view = MetricsView(self.store)
        tabs.addTab(self.metrics_view, "Metrics")
        tabs.setCurrentIndex(0)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.addWidget(tabs)
        self.setCentralWidget(container)
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("S2 Watch")
        self.tray.setIcon(app_icon())
        tray_menu = QMenu(self)
        show_action = tray_menu.addAction("Show S2 Watch")
        show_action.triggered.connect(self.show_from_tray)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_from_tray)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()
        self.adapter: JS8CallTcpAdapter | None = None
        self.magcommstat_adapter: MagCommstatSQLiteAdapter | None = None
        self.commstat_adapter: CommstatSQLiteAdapter | None = None
        self.flmsg_adapter: FLMSGFileAdapter | None = None
        self.superspotter_adapter: JS8SuperSpotterSQLiteAdapter | None = None
        # Reading, matching, analysis and locating run on a background thread.
        self._monitor_forms: list[WatchForm] = []
        self._analyzer: NewsAnalyzer | None = None
        self._worker_thread = QThread(self)
        self._worker = MonitorWorker(str(database_path))
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.start)
        self.worker_config.connect(self._worker.configure)
        self._worker.batch_ready.connect(self.handle_batch)
        self._worker.coverage_ready.connect(self.map_view.set_coverage)
        self._worker_thread.start()
        self.store.disable_all_watch_forms()
        self.refresh_forms()
        self.search_history()
        self.configure_geocoder()
        self.map_filters_changed()
        self.map_legend_changed()
        self.map_radar_changed()
        for overlay in self.map_overlay_filters:
            self.map_overlay_changed(overlay)
        self.map_regions_changed()
        self.map_online_tiles_changed()
        self.connect_js8call()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.poll()

    def create_choice_group(self, choices, selected: str, on_change) -> QActionGroup:
        group = QActionGroup(self)
        group.setExclusive(True)
        for label, value in choices:
            action = group.addAction(label)
            action.setData(value)
            action.setCheckable(True)
            action.setChecked(value == selected)
        if group.checkedAction() is None:
            group.actions()[0].setChecked(True)
        group.triggered.connect(lambda _action: on_change())
        return group

    def create_settings_dialog(self, title: str, layout) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addLayout(layout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        dialog_layout.addWidget(buttons)
        return dialog

    def open_settings_dialog(self, dialog: QDialog) -> None:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About S2 Watch",
            "<h3>S2 Watch</h3>"
            f"<p><b>Version:</b> {APP_VERSION}</p>"
            "<p>Created by <b>N1ROG</b>.</p>"
            "<p>S2 Watch monitors messages from radio communication "
            "applications, applies configurable watch forms, identifies "
            "potentially newsworthy reports, and displays messages and "
            "locations in one place.</p>",
        )

    def connect_js8call(self) -> None:
        self.store.set_setting("js8call.host", self.host.text().strip())
        self.store.set_setting("js8call.port", str(self.port.value()))
        self.store.set_setting("magcommstat.database", self.magcommstat_path.text().strip())
        self.store.set_setting("commstat.database", self.commstat_path.text().strip())
        self.store.set_setting("flmsg.directory", self.flmsg_path.text().strip())
        self.store.set_setting("js8superspotter.database", self.superspotter_path.text().strip())
        self.create_adapters()
        self.rebuild_monitor()
        self.update_application_status()

    def create_adapters(self) -> None:
        # Fresh adapters read the whole review window again; the polling thread closes
        # the replaced ones once it switches to these.
        self.adapter = JS8CallTcpAdapter(self.host.text().strip(), self.port.value())
        lookback = timedelta(hours=int(self.review_duration.currentData()))
        self.magcommstat_adapter = MagCommstatSQLiteAdapter(self.magcommstat_path.text().strip(), lookback)
        self.commstat_adapter = CommstatSQLiteAdapter(self.commstat_path.text().strip(), lookback)
        self.flmsg_adapter = FLMSGFileAdapter(self.flmsg_path.text().strip(), lookback)
        self.superspotter_adapter = JS8SuperSpotterSQLiteAdapter(self.superspotter_path.text().strip(), lookback)

    def rescan_sources(self) -> None:
        """Apply the current watch forms to the whole review window, not just new traffic.

        Each application's backlog is only read once per set of adapters, so without this a
        form enabled after another one never sees the backlog.
        """
        if not self.adapter:
            self.rebuild_monitor()
            return
        self._worker.request_stop()
        self.messages.setRowCount(0)
        self.analysis.setRowCount(0)
        self._report_items.clear()
        self.map_view.clear_locations()
        self.create_adapters()
        self.rebuild_monitor()

    def review_duration_changed(self) -> None:
        self.store.set_setting("review.hours", str(self.review_duration.currentData()))
        self.update_news_label()
        self.messages.setRowCount(0)
        self.analysis.setRowCount(0)
        self._report_items.clear()
        self.map_view.clear_locations()
        self.connect_js8call()

    def time_zone_changed(self) -> None:
        self.store.set_setting("display.time_zone", str(self.time_zone.currentData()))
        self.refresh_display_times()

    def news_rows_changed(self, value: int) -> None:
        self.store.set_setting("news.rows", str(value))
        self.apply_news_rows_limit()

    def apply_news_rows_limit(self) -> None:
        visible_rows = self.news_rows.value()
        for row in range(self.analysis.rowCount()):
            self.analysis.setRowHidden(row, row >= visible_rows)

    def theme_changed(self) -> None:
        theme = str(self.theme.checkedAction().data())
        self.store.set_setting("ui.theme", theme)
        self.apply_theme(theme)

    def apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        palette = app.style().standardPalette()
        if theme == "dark":
            palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#303134"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#3c4043"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#303134"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f1f3f4"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#8ab4f8"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#202124"))
        app.setPalette(palette)

    def format_display_time(self, value: datetime | str) -> str:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if self.time_zone.currentData() == "utc":
            value = value.astimezone(timezone.utc)
        else:
            value = value.astimezone()
        suffix = "Z" if self.time_zone.currentData() == "utc" else "L"
        return f"{value.strftime('%b-%d-%y %H:%M')}{suffix}"

    def refresh_display_times(self) -> None:
        for table in (self.messages, self.analysis):
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None:
                    timestamp = item.data(Qt.ItemDataRole.UserRole)
                    if timestamp:
                        item.setText(self.format_display_time(timestamp))
        self.search_history()

    # Bump to offer category forms added in a later release; forms the user deleted
    # after an earlier installation are not recreated.
    CATEGORY_FORMS_VERSION = 1

    def install_category_forms(self) -> None:
        if int(self.store.get_setting("forms.category_version", "0") or 0) >= self.CATEGORY_FORMS_VERSION:
            return
        existing = {(form.application, form.name) for form in self.store.list_watch_forms()}
        for category, pattern in CATEGORY_PATTERNS.items():
            if (ANY_APPLICATION, category) not in existing:
                self.store.save_watch_form(
                    WatchForm(ANY_APPLICATION, category, pattern, enabled=False, use_regex=True)
                )
        self.store.set_setting("forms.category_version", str(self.CATEGORY_FORMS_VERSION))

    def refresh_forms(self) -> None:
        self.install_category_forms()
        forms = self.store.list_watch_forms()
        existing_forms = {(form.application, form.name) for form in forms}
        for application, name in self.DEFAULT_WATCH_FORMS.items():
            if (application, name) not in existing_forms:
                default = WatchForm(application, name, "", enabled=False)
                self.store.save_watch_form(default)
                forms.append(default)
        self.clear_live_messages()
        self.forms.blockSignals(True)
        self.forms.clear()
        for form in forms:
            filter_description = form.match_text or "(all messages)"
            item = QListWidgetItem(
                f"{form.application} | {form.name}: {filter_description}"
            )
            item.setData(256, form)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if form.enabled else Qt.CheckState.Unchecked)
            self.forms.addItem(item)
        self.forms.blockSignals(False)
        self.rescan_sources()

    def rebuild_monitor(self) -> None:
        if self.adapter:
            adapters = [self.adapter]
            if self.magcommstat_adapter:
                adapters.append(self.magcommstat_adapter)
            if self.commstat_adapter:
                adapters.append(self.commstat_adapter)
            if self.flmsg_adapter:
                adapters.append(self.flmsg_adapter)
            if self.superspotter_adapter:
                adapters.append(self.superspotter_adapter)
            self._monitor_forms = self.store.list_watch_forms()
            self._analyzer = self.create_analyzer()
            self.worker_config.emit(MonitorConfig(
                adapters, self._monitor_forms, self._analyzer, self.geocoding_enabled.isChecked(),
            ))

    def create_analyzer(self) -> NewsAnalyzer | None:
        mode = self.analysis_mode.checkedAction().data()
        if mode == "rules":
            return RuleBasedNewsAnalyzer()
        return FallbackNewsAnalyzer(
            OllamaNewsAnalyzer(
                model=self.ollama_model.text().strip() or "llama3.2",
                endpoint=self.ollama_endpoint.text().strip() or "http://127.0.0.1:11434",
            ),
            RuleBasedNewsAnalyzer(),
        )

    def analysis_mode_changed(self) -> None:
        self.store.set_setting("analysis.mode", str(self.analysis_mode.checkedAction().data()))
        self.rebuild_monitor()

    def analysis_settings_changed(self) -> None:
        self.store.set_setting("ollama.model", self.ollama_model.text().strip())
        self.store.set_setting("ollama.endpoint", self.ollama_endpoint.text().strip())
        self.rebuild_monitor()

    def add_form(self) -> None:
        dialog = WatchFormDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        form = dialog.watch_form()
        try:
            self.store.save_watch_form(form)
        except sqlite3.Error as error:
            QMessageBox.critical(self, "Unable to save watch form", str(error))
            return
        self.refresh_forms()

    def edit_form(self) -> None:
        item = self.forms.currentItem()
        if item is None:
            return
        current = item.data(256)
        if not isinstance(current, WatchForm):
            return
        dialog = WatchFormDialog(self, current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.watch_form()
        try:
            if updated.name != current.name:
                self.store.delete_watch_form(current.application, current.name)
            self.store.save_watch_form(updated)
        except sqlite3.Error as error:
            QMessageBox.critical(self, "Unable to save watch form", str(error))
            return
        self.refresh_forms()

    def form_changed(self, item: QListWidgetItem) -> None:
        form = item.data(256)
        if isinstance(form, WatchForm):
            self.store.save_watch_form(
                WatchForm(
                    form.application,
                    form.name,
                    form.match_text,
                    item.checkState() == Qt.CheckState.Checked,
                    form.use_regex,
                    form.message_type,
                    form.sender,
                )
            )
            self.rescan_sources()
            self.poll()

    def remove_form(self) -> None:
        item = self.forms.currentItem()
        if item is None:
            return
        form = item.data(256)
        if isinstance(form, WatchForm):
            self.store.delete_watch_form(form.application, form.name)
            self.refresh_forms()

    def import_forms(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import watch forms", "", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            forms = forms_from_json(Path(path).read_text(encoding="utf-8"))
            for form in forms:
                self.store.save_watch_form(form)
        except (OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.critical(self, "Unable to import watch forms", str(error))
            return
        self.refresh_forms()
        QMessageBox.information(self, "Watch forms imported", f"Imported {len(forms)} watch form(s).")

    def export_forms(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export watch forms", "s2-watch-forms.json", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            Path(path).write_text(
                forms_to_json(self.store.list_watch_forms()), encoding="utf-8"
            )
        except OSError as error:
            QMessageBox.critical(self, "Unable to export watch forms", str(error))
            return
        QMessageBox.information(self, "Watch forms exported", f"Exported watch forms to:\n{path}")

    def poll(self) -> None:
        self.update_application_status()
        monitored = self.monitored_applications()
        statuses = []
        for application, adapter in (
            ("JS8Call", self.adapter),
            ("MagCommstat", self.magcommstat_adapter),
            ("Commstat", self.commstat_adapter),
            ("FLMSG", self.flmsg_adapter),
            ("JS8SuperSpotter", self.superspotter_adapter),
        ):
            if adapter:
                status = self.application_status(application, adapter, monitored)
                statuses.append(f"{application}: {'not monitored' if status == self.NOT_MONITORED else status}")
        if statuses:
            text = " | ".join(statuses)
            if not monitored:
                text = "No watch forms enabled — Configuration → Watch Forms… | " + text
            self.status.setText(text)
        analyzer = self._analyzer
        if isinstance(analyzer, FallbackNewsAnalyzer) and analyzer.last_error:
            self.status.setText(f"{self.status.text()}; AI fallback: {analyzer.last_error}")

    def update_application_status(self) -> None:
        adapters = {
            "JS8Call": self.adapter,
            "MagCommstat": self.magcommstat_adapter,
            "Commstat": self.commstat_adapter,
            "FLMSG": self.flmsg_adapter,
            "JS8SuperSpotter": self.superspotter_adapter,
        }
        monitored = self.monitored_applications()
        for application, adapter in adapters.items():
            status = self.application_status(application, adapter, monitored)
            connected = status == "Connected" or status.startswith("Connected to ")
            if application not in monitored:
                color = "#757575"
            else:
                color = "#2e7d32" if connected else "#c62828"
            self.application_status_lights[application].setText(f"● {application}: {status}")
            self.application_status_lights[application].setStyleSheet(f"color: {color};")

    NOT_MONITORED = "Not monitored — no watch form enabled"

    def monitored_applications(self) -> set[str]:
        applications = {form.application for form in self._monitor_forms if form.enabled}
        if ANY_APPLICATION in applications:
            return set(self.application_status_lights)
        return applications

    def application_status(self, application: str, adapter, monitored: set[str]) -> str:
        # Adapters for applications without an enabled watch form are never polled, so
        # their own status would read "Disconnected" even when the application is running.
        if application not in monitored:
            return self.NOT_MONITORED
        return getattr(adapter, "status", "Disconnected") if adapter else "Disconnected"

    def handle_batch(self, batch: list[ProcessedMessage]) -> None:
        # Insert the whole batch with sorting off, then sort and trim once: re-sorting
        # after every row made large backlogs freeze the window.
        alerts: list[AnalysisResult] = []
        live_sorting = self.messages.isSortingEnabled()
        analysis_sorting = self.analysis.isSortingEnabled()
        self.messages.setSortingEnabled(False)
        self.analysis.setSortingEnabled(False)
        for processed in batch:
            self.insert_live_row(processed.message)
            report = processed.report
            shown = self.shown_report_item(report.report_id) if report else None
            if shown is not None:
                # Another copy of a report already on screen: count it, don't repeat it.
                self.analysis.item(shown.row(), self.SEEN_COLUMN).setText(self.seen_text(report))
                continue
            self.map_view.add_message(processed.message, processed.locations,
                                      dimmed=bool(report and report.status == "ignored"))
            if processed.result:
                item = self.insert_analysis_row(processed.message, processed.result, report)
                if report:
                    self._report_items[report.report_id] = item
                # Alert once per report, and not again for reports re-read by a re-scan.
                ignored = report is not None and report.status == "ignored"
                if (report is None or report.new_report) and not ignored and self.should_alert(processed.result):
                    alerts.append(processed.result)
        self.trim_oldest_rows(self.messages, self.LIVE_ROWS_LIMIT)
        if self.trim_oldest_rows(self.analysis, self.ANALYSIS_ROWS_LIMIT):
            self._report_items = {
                int(item.data(self.REPORT_ROLE)): item
                for row in range(self.analysis.rowCount())
                if (item := self.analysis.item(row, 0)) is not None and item.data(self.REPORT_ROLE) is not None
            }
        self.messages.setSortingEnabled(live_sorting)
        self.analysis.setSortingEnabled(analysis_sorting)
        if any(processed.result for processed in batch):
            self.analysis.sortItems(0, Qt.SortOrder.DescendingOrder)
            self.apply_news_rows_limit()
        self.show_alerts(alerts)
        if any(processed.report for processed in batch):
            self.metrics_view.data_changed()

    def shown_report_item(self, report_id: int) -> QTableWidgetItem | None:
        item = self._report_items.get(report_id)
        if item is not None and item.tableWidget() is self.analysis:
            return item
        self._report_items.pop(report_id, None)
        return None

    @staticmethod
    def seen_text(report) -> str:
        if report is None or report.copies <= 1:
            return "1×"
        return f"{report.copies}× · {', '.join(report.sources)}"

    @staticmethod
    def trim_oldest_rows(table: QTableWidget, limit: int) -> bool:
        excess = table.rowCount() - limit
        if excess <= 0:
            return False
        rows = sorted(
            range(table.rowCount()),
            key=lambda row: str(table.item(row, 0).data(Qt.ItemDataRole.UserRole)) if table.item(row, 0) else "",
        )
        for row in sorted(rows[:excess], reverse=True):
            table.removeRow(row)
        return True

    def show_match(self, message: Message, locations=None) -> None:
        sorting_enabled = self.messages.isSortingEnabled()
        self.messages.setSortingEnabled(False)
        self.insert_live_row(message)
        self.trim_oldest_rows(self.messages, self.LIVE_ROWS_LIMIT)
        self.messages.setSortingEnabled(sorting_enabled)
        self.map_view.add_message(message, locations)

    def insert_live_row(self, message: Message) -> None:
        row = self.messages.rowCount()
        self.messages.insertRow(row)
        values = (
            self.format_display_time(message.received_at),
            message.source_application,
            message.form_name or "",
            message.raw_content,
        )
        for column, value in enumerate(values):
            item = SortableTableItem(value) if column == 0 else QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, message.received_at.isoformat())
            self.messages.setItem(row, column, item)

    def clear_live_messages(self) -> None:
        self.messages.setRowCount(0)

    def show_message_details(self, row: int, _column: int) -> None:
        details = []
        headers = (
            "Time",
            "Application",
            "Watch form",
            "Message",
        )
        for column, header in enumerate(headers):
            item = self.messages.item(row, column)
            details.append(f"<b>{header}:</b> {escape(item.text() if item else '')}")
        QMessageBox.information(
            self,
            "Message details",
            "<br>".join(details),
        )

    def show_analysis_details(self, row: int, _column: int) -> None:
        headers = self.ANALYSIS_COLUMNS
        details = []
        for column, header in enumerate(headers):
            item = self.analysis.item(row, column)
            value = escape(item.text() if item else "")
            if header in {"Summary", "Message"}:
                value = f"<div style='white-space:pre-wrap'>{value}</div>"
            details.append(
                f"<tr><td style='padding:4px 12px 4px 0'><b>{header}</b></td>"
                f"<td style='padding:4px 0'>{value}</td></tr>"
            )
        QMessageBox.information(
            self,
            "Newsworthiness analysis details",
            "<table>" + "".join(details) + "</table>",
        )

    def configure_geocoder(self) -> None:
        # Geocoding runs on the polling thread, which gets the setting with its configuration.
        self.rebuild_monitor()

    def geocoding_setting_changed(self) -> None:
        self.store.set_setting("geocoding.enabled", "1" if self.geocoding_enabled.isChecked() else "0")
        self.configure_geocoder()

    def map_filters_changed(self) -> None:
        applications = {
            application for application, checkbox in self.map_application_filters.items()
            if checkbox.isChecked()
        }
        kinds = set()
        if self.map_station_filter.isChecked():
            kinds.add("station")
        if self.map_news_filter.isChecked():
            kinds.add("news")
        if self.map_region_location_filter.isChecked():
            kinds.add("region")
        categories = {
            category for category, checkbox in self.map_category_filters.items()
            if checkbox.isChecked()
        }
        for application, checkbox in self.map_application_filters.items():
            self.store.set_setting(
                f"map.filter.application.{application}",
                "1" if checkbox.isChecked() else "0",
            )
        for category, checkbox in self.map_category_filters.items():
            self.store.set_setting(
                f"map.filter.category.{category}",
                "1" if checkbox.isChecked() else "0",
            )
        self.map_view.set_filters(applications, kinds, categories)

    def map_legend_changed(self) -> None:
        visible = self.map_legend_filter.isChecked()
        self.store.set_setting("map.legend.visible", "1" if visible else "0")
        self.map_view.set_legend_visible(visible)

    def map_online_tiles_changed(self) -> None:
        enabled = self.map_online_tiles.isChecked()
        self.store.set_setting("map.tiles.online", "1" if enabled else "0")
        self.map_view.set_online_tiles(enabled)

    def map_regions_changed(self) -> None:
        visible = self.map_regions_filter.isChecked()
        self.store.set_setting("map.regions.visible", "1" if visible else "0")
        self.map_view.set_regions_visible(visible)

    def map_overlay_changed(self, overlay: str) -> None:
        visible = self.map_overlay_filters[overlay].isChecked()
        self.store.set_setting(f"map.{overlay}.visible", "1" if visible else "0")
        self.map_view.set_overlay_visible(overlay, visible)
        if overlay == "nws":
            self.nws_poller.set_enabled(visible)

    def update_news_label(self) -> None:
        # The table holds reports from the review window read at startup plus new traffic.
        self.news_label.setText(
            f"<b>Newsworthiness</b> — reports from the last {self.review_duration.currentText()} "
            f"<span style='color:gray'>(review window; change under Configuration → Sources…)</span>"
        )

    def map_radar_changed(self) -> None:
        visible = self.map_radar_filter.isChecked()
        self.store.set_setting("map.radar.visible", "1" if visible else "0")
        self.map_view.set_radar_visible(visible)

    def show_analysis(self, message: Message, result: AnalysisResult) -> None:
        sorting_enabled = self.analysis.isSortingEnabled()
        self.analysis.setSortingEnabled(False)
        self.insert_analysis_row(message, result)
        self.trim_oldest_rows(self.analysis, self.ANALYSIS_ROWS_LIMIT)
        self.analysis.setSortingEnabled(sorting_enabled)
        self.analysis.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.apply_news_rows_limit()
        self.show_alerts([result] if self.should_alert(result) else [])

    def insert_analysis_row(self, message: Message, result: AnalysisResult, report=None) -> QTableWidgetItem:
        label = "NEWSWORTHY" if result.newsworthy else "routine"
        row = self.analysis.rowCount()
        self.analysis.insertRow(row)
        values = (
            self.format_display_time(message.received_at),
            message.source_application,
            message.form_name or "",
            label,
            self.status_text(report.status, report.note) if report else "",
            result.category,
            result.urgency,
            f"{result.confidence:.0%}",
            self.seen_text(report),
            result.summary,
            message.raw_content,
        )
        first = None
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, message.received_at.isoformat())
                item.setData(Qt.ItemDataRole.UserRole + 1, message)
                if report is not None:
                    item.setData(self.REPORT_ROLE, report.report_id)
                first = item
            self.analysis.setItem(row, column, item)
        self.style_report_row(row, report.status if report else "")
        self.map_view.set_message_category(message, result.category if result.newsworthy else "routine")
        return first

    # -- report labels (triage) -------------------------------------------------------

    def status_text(self, status: str, note: str = "") -> str:
        label = self.STATUS_LABELS.get(status, "")
        return f"{label} · {note}" if label and note else label

    def style_report_row(self, row: int, status: str) -> None:
        """Ignored reports stay visible but greyed out; labelled ones show their status in bold."""
        muted = self.analysis.palette().color(QPalette.ColorRole.PlaceholderText)
        for column in range(self.analysis.columnCount()):
            item = self.analysis.item(row, column)
            if item is None:
                continue
            item.setForeground(QBrush(muted) if status == "ignored" else QBrush())
            font = QFont(item.font())
            font.setItalic(status == "ignored")
            font.setBold(column == self.STATUS_COLUMN and status in ("interest", "followup"))
            item.setFont(font)

    def selected_report_rows(self) -> list[tuple[int, int]]:
        """(row, report id) for each selected Newsworthiness row that belongs to a report."""
        rows = []
        for index in self.analysis.selectionModel().selectedRows():
            item = self.analysis.item(index.row(), 0)
            report_id = item.data(self.REPORT_ROLE) if item else None
            if report_id is not None:
                rows.append((index.row(), int(report_id)))
        return rows

    def show_report_menu(self, position) -> None:
        rows = self.selected_report_rows()
        if not rows:
            return
        menu = QMenu(self)
        heading = menu.addAction(f"{len(rows)} report{'s' if len(rows) != 1 else ''} selected")
        heading.setEnabled(False)
        mark = menu.addMenu("Mark as")
        for action in self.report_actions:
            mark.addAction(action)
        menu.addAction(self.edit_note_action)
        menu.exec(self.analysis.viewport().mapToGlobal(position))

    def mark_selected_reports(self, status: str) -> None:
        rows = self.selected_report_rows()
        if not rows:
            return
        note = None  # keep existing notes
        if status == "followup":
            existing = self.store.report_status(rows[0][1])[1] if len(rows) == 1 else ""
            text, accepted = QInputDialog.getText(
                self, "Follow-up created",
                f"Optional note for {len(rows)} report{'s' if len(rows) != 1 else ''} "
                "(e.g. your follow-up report number):", text=existing,
            )
            if not accepted:
                return
            note = text.strip()
        elif status == "":
            note = ""
        self.apply_report_status([report_id for _row, report_id in rows], status, note)

    def edit_selected_note(self) -> None:
        rows = self.selected_report_rows()
        if not rows:
            return
        existing = self.store.report_status(rows[0][1])[1] if len(rows) == 1 else ""
        text, accepted = QInputDialog.getText(self, "Report note", "Note:", text=existing)
        if not accepted:
            return
        for _row, report_id in rows:
            status = self.store.report_status(report_id)[0]
            self.store.set_report_status([report_id], status, text.strip())
        self.refresh_report_rows({report_id for _row, report_id in rows})
        self.analysis.resizeColumnToContents(self.STATUS_COLUMN)

    def apply_report_status(self, report_ids: list[int], status: str, note: str | None) -> None:
        self.store.set_report_status(report_ids, status, note)
        self.refresh_report_rows(set(report_ids))
        self.analysis.resizeColumnToContents(self.STATUS_COLUMN)
        self.metrics_view.data_changed()

    def refresh_report_rows(self, report_ids: set[int]) -> None:
        for row in range(self.analysis.rowCount()):
            item = self.analysis.item(row, 0)
            report_id = item.data(self.REPORT_ROLE) if item else None
            if report_id is None or int(report_id) not in report_ids:
                continue
            status, note = self.store.report_status(int(report_id))
            self.analysis.item(row, self.STATUS_COLUMN).setText(self.status_text(status, note))
            self.style_report_row(row, status)
            message = item.data(Qt.ItemDataRole.UserRole + 1)
            if isinstance(message, Message):
                self.map_view.set_message_dimmed(message, status == "ignored")

    def should_alert(self, result: AnalysisResult) -> bool:
        if not result.newsworthy or not self.alerts_enabled.isChecked():
            return False
        return not (self.alert_high_urgency.isChecked() and result.urgency != "high")

    def show_alerts(self, alerts: list[AnalysisResult]) -> None:
        if not alerts:
            return
        high = any(result.urgency == "high" for result in alerts)
        icon = QSystemTrayIcon.MessageIcon.Warning if high else QSystemTrayIcon.MessageIcon.Information
        if len(alerts) <= 3:
            for result in alerts:
                self.tray.showMessage(
                    f"S2 Watch: {result.category.replace('_', ' ').title()}", result.summary,
                    QSystemTrayIcon.MessageIcon.Warning if result.urgency == "high"
                    else QSystemTrayIcon.MessageIcon.Information,
                    10000,
                )
            return
        # A backlog can hold hundreds of newsworthy messages; summarise instead of flooding.
        categories = ", ".join(sorted({result.category for result in alerts}))
        self.tray.showMessage(
            f"S2 Watch: {len(alerts)} newsworthy messages",
            f"Categories: {categories}. See the Newsworthiness table.", icon, 10000,
        )

    def focus_analysis_message(self, row: int, _column: int) -> None:
        item = self.analysis.item(row, 0)
        message = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
        if isinstance(message, Message):
            self.map_view.focus_message(message)

    def select_analysis_message(self, message_id: str) -> None:
        for row in range(self.analysis.rowCount()):
            item = self.analysis.item(row, 0)
            message = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
            if isinstance(message, Message) and str(id(message)) == message_id:
                self.analysis.selectRow(row)
                self.analysis.scrollToItem(item)
                break

    def alert_settings_changed(self) -> None:
        self.store.set_setting("alerts.enabled", "1" if self.alerts_enabled.isChecked() else "0")
        self.store.set_setting(
            "alerts.high_urgency_only",
            "1" if self.alert_high_urgency.isChecked() else "0",
        )

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_from_tray()

    def quit_from_tray(self) -> None:
        self.close()

    def search_history(self) -> None:
        self.history.clear()
        rows = self.store.search_history(
            text=self.history_search.text().strip(),
            category=str(self.history_category.currentData()),
            urgency=str(self.history_urgency.currentData()),
            newsworthy_only=self.history_newsworthy.isChecked(),
        )
        for received_at, application, form_name, content, category, confidence in rows:
            confidence_text = f"{confidence:.0%}" if confidence is not None else "n/a"
            self.history.addItem(
                f"[{self.format_display_time(received_at)}] {application} | {form_name or 'unassigned'} | "
                f"{category or 'unanalyzed'} | {confidence_text} | {content}"
            )

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.nws_poller.set_enabled(False)
        # The polling thread owns the adapters and its own database connection.
        self._worker.request_stop()
        QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)
        self._worker_thread.quit()
        self._worker_thread.wait(5000)
        self.tray.hide()
        self.store.close()
        event.accept()
        # Open settings windows would otherwise keep the application alive.
        QApplication.quit()
