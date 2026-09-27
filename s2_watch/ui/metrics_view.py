"""Metrics tab: what share of newsworthy reports each category, area, and source accounts for.

Everything counts *reports*, not messages: copies of one report that arrived through
several feeders or relays count once (see ``s2_watch.dedup``). Percentages are shares
of newsworthy reports first seen in the chosen period.

Charts follow one rule set: a single measure uses one hue (identity comes from the
row label, not colour), values are written beside the marks, hovering a row shows its
exact numbers, and colours are stepped separately for the light and dark themes. The
two-series source chart uses the validated blue/orange pair (checked for colour-vision
deficiency and contrast against both theme surfaces).
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QToolTip, QVBoxLayout, QWidget,
)

from s2_watch.analysis.categories import NEWS_CATEGORIES
from s2_watch.storage import SQLiteStore, utc_iso

PERIODS = (("Last 24 hours", timedelta(hours=24)), ("Last 7 days", timedelta(days=7)),
           ("Last 30 days", timedelta(days=30)))
SOURCES = ("JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter")
# Validated series colours (light surface #ffffff, dark surface #303134).
SERIES = {"light": ("#2a78d6", "#eb6834"), "dark": ("#3987e5", "#d95926")}


def is_dark(widget: QWidget) -> bool:
    return widget.palette().color(QPalette.ColorRole.Base).lightness() < 128


def series_color(widget: QWidget, index: int) -> QColor:
    return QColor(SERIES["dark" if is_dark(widget) else "light"][index])


def text_colors(widget: QWidget) -> tuple[QColor, QColor]:
    primary = widget.palette().color(QPalette.ColorRole.Text)
    secondary = QColor(primary)
    secondary.setAlphaF(0.68)
    return primary, secondary


def percent(part: int, whole: int) -> str:
    if not whole:
        return "0%"
    share = 100 * part / whole
    return f"{share:.0f}%" if share >= 1 or share == 0 else "<1%"


def rounded_end_bar(rect: QRectF) -> QPainterPath:
    """A bar anchored at its left (baseline) with a 4 px rounded data end."""
    path = QPainterPath()
    radius = min(4.0, rect.width() / 2, rect.height() / 2)
    path.addRoundedRect(rect, radius, radius)
    if rect.width() > radius:
        square = QPainterPath()
        square.addRect(QRectF(rect.left(), rect.top(), radius, rect.height()))
        path = path.united(square)
    return path


class BarChart(QWidget):
    """Horizontal bars: one row per label, one hue, or two stacked series with a legend."""

    ROW = 24
    BAR = 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[tuple[str, tuple[int, ...], str, str]] = []  # label, values, value text, tooltip
        self.series_names: tuple[str, ...] = ()
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_rows(self, rows, series_names: tuple[str, ...] = ()) -> None:
        self.rows = list(rows)
        self.series_names = series_names
        self.setFixedHeight(self.sizeHint().height())
        self.update()

    def legend_height(self) -> int:
        return 22 if len(self.series_names) > 1 else 0

    def sizeHint(self) -> QSize:
        return QSize(420, self.legend_height() + max(1, len(self.rows)) * self.ROW + 4)

    def _geometry(self):
        metrics = self.fontMetrics()
        label_width = max([metrics.horizontalAdvance(row[0]) for row in self.rows] + [40]) + 12
        value_width = max([metrics.horizontalAdvance(row[2]) for row in self.rows] + [30]) + 10
        bar_width = max(40, self.width() - label_width - value_width - 8)
        return label_width, bar_width

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        primary, secondary = text_colors(self)
        top = self.legend_height()
        if top:
            x = 0
            for index, name in enumerate(self.series_names):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(series_color(self, index))
                painter.drawRoundedRect(QRectF(x, 5, 12, 12), 3, 3)
                painter.setPen(secondary)
                painter.drawText(QRectF(x + 17, 0, 200, 22), Qt.AlignmentFlag.AlignVCenter, name)
                x += 17 + self.fontMetrics().horizontalAdvance(name) + 18
        if not self.rows:
            painter.setPen(secondary)
            painter.drawText(self.rect().adjusted(0, top, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             "No newsworthy reports in this period.")
            return
        label_width, bar_width = self._geometry()
        largest = max(sum(row[1]) for row in self.rows) or 1
        baseline = QColor(secondary)
        baseline.setAlphaF(0.35)
        for index, (label, values, value_text, _tip) in enumerate(self.rows):
            y = top + index * self.ROW
            painter.setPen(primary)
            painter.drawText(QRectF(0, y, label_width - 8, self.ROW),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            x = float(label_width)
            bar_top = y + (self.ROW - self.BAR) / 2
            painter.setPen(Qt.PenStyle.NoPen)
            for series, value in enumerate(values):
                if value <= 0:
                    continue
                width = max(2.0, bar_width * value / largest)
                # 2 px surface gap between stacked segments.
                gap = 2.0 if x > label_width else 0.0
                painter.setBrush(series_color(self, series))
                last = series == len(values) - 1 or not any(values[series + 1:])
                rect = QRectF(x + gap, bar_top, max(1.0, width - gap), self.BAR)
                if last:
                    painter.drawPath(rounded_end_bar(rect))
                else:
                    painter.drawRect(rect)
                x += width
            painter.setPen(baseline)
            painter.drawLine(label_width, int(y + 3), label_width, int(y + self.ROW - 3))
            painter.setPen(secondary)
            painter.drawText(QRectF(x + 6, y, 200, self.ROW), Qt.AlignmentFlag.AlignVCenter, value_text)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = int((event.position().y() - self.legend_height()) // self.ROW) if hasattr(event, "position") \
                else int((event.pos().y() - self.legend_height()) // self.ROW)
            if 0 <= index < len(self.rows):
                QToolTip.showText(event.globalPos(), self.rows[index][3], self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class ColumnMultiples(QWidget):
    """Small multiples: one mini column chart per category, all on the same scale."""

    CELL_HEIGHT = 100

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.panels: list[tuple[str, list[int]]] = []
        self.buckets: list[str] = []
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, panels, buckets) -> None:
        self.panels, self.buckets = list(panels), list(buckets)
        rows = (len(self.panels) + 1) // 2
        self.setFixedHeight(max(1, rows) * self.CELL_HEIGHT)
        self.update()

    def _cells(self):
        width = (self.width() - 24) / 2
        for index, panel in enumerate(self.panels):
            column, row = index % 2, index // 2
            yield panel, QRectF(column * (width + 24), row * self.CELL_HEIGHT, width, self.CELL_HEIGHT - 26)

    def paintEvent(self, _event) -> None:
        if not self.panels:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        primary, secondary = text_colors(self)
        largest = max((max(counts) for _name, counts in self.panels if counts), default=0) or 1
        color = series_color(self, 0)
        baseline = QColor(secondary)
        baseline.setAlphaF(0.35)
        for (name, counts), cell in self._cells():
            painter.setPen(primary)
            painter.drawText(QRectF(cell.left(), cell.top(), cell.width(), 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            painter.setPen(secondary)
            painter.drawText(QRectF(cell.left(), cell.top(), cell.width(), 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(sum(counts)))
            plot = QRectF(cell.left(), cell.top() + 22, cell.width(), cell.height() - 24)
            painter.setPen(baseline)
            painter.drawLine(int(plot.left()), int(plot.bottom()), int(plot.right()), int(plot.bottom()))
            if self.buckets:
                # Time axis: where the period starts and ends.
                painter.setPen(secondary)
                axis = QRectF(plot.left(), plot.bottom() + 2, plot.width(), 14)
                painter.drawText(axis, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.buckets[0])
                painter.drawText(axis, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.buckets[-1])
            if not counts:
                continue
            slot = plot.width() / len(counts)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            for index, count in enumerate(counts):
                if not count:
                    continue
                height = max(2.0, plot.height() * count / largest)
                bar = QRectF(plot.left() + index * slot + 1, plot.bottom() - height, max(1.0, slot - 2), height)
                path = QPainterPath()
                radius = min(4.0, bar.width() / 2, bar.height())
                path.addRoundedRect(bar, radius, radius)
                square = QPainterPath()
                square.addRect(QRectF(bar.left(), bar.bottom() - radius, bar.width(), radius))
                painter.drawPath(path.united(square))

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            point = event.pos()
            for (name, counts), cell in self._cells():
                if cell.contains(point.x(), point.y()) and counts:
                    slot = cell.width() / len(counts)
                    index = min(len(counts) - 1, max(0, int((point.x() - cell.left()) // slot)))
                    QToolTip.showText(event.globalPos(), f"{name}\n{self.buckets[index]}: {counts[index]} report(s)", self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(event)


class StatTile(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.title = QLabel(title)
        self.value = QLabel("–")
        font = QFont(self.value.font())
        font.setPointSizeF(font.pointSizeF() * 1.8)
        font.setBold(True)
        self.value.setFont(font)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        for label in (self.title, self.value, self.detail):
            layout.addWidget(label)

    def set(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class MetricsView(QScrollArea):
    def __init__(self, store: SQLiteStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Period:"))
        self.period = QComboBox()
        for label, span in PERIODS:
            self.period.addItem(label, span)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.period)
        controls.addSpacing(16)
        self.scope_label = QLabel("Scope:")
        controls.addWidget(self.scope_label)
        self.scope = QComboBox()
        self.scope.addItem("My focus regions", "focus")
        self.scope.addItem("All of the U.S.", "all")
        self.scope.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.scope)
        self.focus_regions: set[int] = set()
        self.set_focus_regions(set())
        controls.addStretch(1)
        self.updated = QLabel("")
        controls.addWidget(self.updated)
        layout.addLayout(controls)

        tiles = QHBoxLayout()
        self.reports_tile = StatTile("Newsworthy reports")
        self.duplicates_tile = StatTile("Duplicate copies merged")
        self.top_tile = StatTile("Largest category")
        for tile in (self.reports_tile, self.duplicates_tile, self.top_tile):
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)

        def section(title: str, note: str = "") -> None:
            heading = QLabel(f"<b>{title}</b>" + (f" <span style='color:gray'>{note}</span>" if note else ""))
            heading.setContentsMargins(0, 14, 0, 2)
            layout.addWidget(heading)

        section("Share by category", "— percent of newsworthy reports")
        self.categories = BarChart()
        layout.addWidget(self.categories)
        section("Trend", "— newsworthy reports per category over the period, same scale in every panel")
        self.trend = ColumnMultiples()
        layout.addWidget(self.trend)
        area = QGridLayout()
        region_box, state_box = QVBoxLayout(), QVBoxLayout()
        region_box.addWidget(QLabel("<b>By MARS region</b>"))
        self.regions = BarChart()
        region_box.addWidget(self.regions)
        region_box.addStretch(1)
        state_box.addWidget(QLabel("<b>By state</b> <span style='color:gray'>— top 12</span>"))
        self.states = BarChart()
        state_box.addWidget(self.states)
        state_box.addStretch(1)
        area.addLayout(region_box, 0, 0)
        area.addLayout(state_box, 0, 1)
        area.setContentsMargins(0, 14, 0, 0)
        layout.addLayout(area)
        section("By source", "— which feeder reported each newsworthy report first, and the extra copies each added")
        self.sources = BarChart()
        layout.addWidget(self.sources)
        section("Top reporting stations", "— stations that sent the first copy")
        self.stations = BarChart()
        layout.addWidget(self.stations)
        layout.addStretch(1)
        self.setWidget(body)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._periodic = QTimer(self)
        self._periodic.setInterval(60_000)
        self._periodic.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._periodic.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._periodic.stop()

    def set_focus_regions(self, regions: set[int]) -> None:
        """Offer a 'My focus regions' scope when the operator has chosen regions."""
        self.focus_regions = set(regions)
        has_focus = bool(self.focus_regions)
        self.scope_label.setVisible(has_focus)
        self.scope.setVisible(has_focus)
        if has_focus:
            self.scope.setItemText(0, "My focus regions (" + ", ".join(f"R{r}" for r in sorted(self.focus_regions)) + ")")
        if self.isVisible():
            self.refresh()

    def _in_scope(self, region) -> bool:
        return not self.focus_regions or self.scope.currentData() == "all" or region in self.focus_regions

    def data_changed(self) -> None:
        """New reports arrived; refresh soon if the tab is visible."""
        if self.isVisible():
            self._refresh_timer.start()

    def refresh(self) -> None:
        span: timedelta = self.period.currentData()
        now = datetime.now(timezone.utc)
        since = utc_iso(now - span)
        regions = None if not self.focus_regions or self.scope.currentData() == "all" else self.focus_regions
        total, newsworthy, copies = self.store.report_totals(since, regions)
        reports = [report for report in self.store.newsworthy_reports(since) if self._in_scope(report["region"])]
        by_source_copies = self.store.copies_by_source(since, regions)
        self.updated.setText(f"Updated {datetime.now():%H:%M:%S}")

        merged = copies - total
        ignored = self.store.ignored_reports(since, regions)
        self.reports_tile.set(
            str(newsworthy),
            f"of {total} reports captured" + (f" · {ignored} ignored, not counted" if ignored else ""),
        )
        self.duplicates_tile.set(
            str(merged), f"{percent(merged, copies)} of {copies} copies received were repeats" if copies else "no copies yet")
        categories = Counter(report["category"] for report in reports)
        if categories:
            top, count = categories.most_common(1)[0]
            self.top_tile.set(percent(count, newsworthy), f"{top} · {count} of {newsworthy}")
        else:
            self.top_tile.set("–", "no newsworthy reports")

        self.categories.set_rows(
            [(name, (categories[name],), f"{percent(categories[name], newsworthy)} · {categories[name]}",
              f"{name}: {categories[name]} of {newsworthy} newsworthy reports ({percent(categories[name], newsworthy)})")
             for name in NEWS_CATEGORIES] if newsworthy else [])

        hourly = span <= timedelta(hours=24)
        step = timedelta(hours=1) if hourly else timedelta(days=1)
        local_now = datetime.now().astimezone()
        start = (local_now - span).replace(minute=0, second=0, microsecond=0)
        if not hourly:
            start = start.replace(hour=0)
        buckets = []
        cursor = start
        while cursor <= local_now:
            buckets.append(cursor)
            cursor += step
        trend = {name: [0] * len(buckets) for name in NEWS_CATEGORIES}
        for report in reports:
            seen = datetime.fromisoformat(report["first_seen"]).astimezone()
            index = int((seen - start) // step)
            if 0 <= index < len(buckets) and report["category"] in trend:
                trend[report["category"]][index] += 1
        labels = [bucket.strftime("%a %H:00" if hourly else "%a %b %d") for bucket in buckets]
        self.trend.set_data([(name, trend[name]) for name in NEWS_CATEGORIES] if newsworthy else [], labels)

        regions = Counter(report["region"] for report in reports)
        region_rows = [(f"Region {region}", (regions[region],), f"{percent(regions[region], newsworthy)} · {regions[region]}",
                        f"MARS Region {region} (MR{region:02d}): {regions[region]} of {newsworthy} reports")
                       for region in range(1, 11)]
        if regions[None]:
            region_rows.append(("Unknown", (regions[None],), f"{percent(regions[None], newsworthy)} · {regions[None]}",
                                f"No location found: {regions[None]} of {newsworthy} reports"))
        self.regions.set_rows(region_rows if newsworthy else [])

        states = Counter(report["state"] or "" for report in reports)
        known = [(state, count) for state, count in states.most_common() if state]
        state_rows = [(state, (count,), f"{percent(count, newsworthy)} · {count}", f"{state}: {count} of {newsworthy} reports")
                      for state, count in known[:12]]
        other = sum(count for _state, count in known[12:])
        if other:
            state_rows.append(("Other", (other,), f"{percent(other, newsworthy)} · {other}", f"Other states: {other} reports"))
        if states[""]:
            state_rows.append(("Unknown", (states[""],), f"{percent(states[''], newsworthy)} · {states['']}",
                               f"No state found: {states['']} of {newsworthy} reports"))
        self.states.set_rows(state_rows if newsworthy else [])

        firsts = Counter(report["first_source"] for report in reports)
        source_rows = []
        for source in SOURCES:
            first = firsts[source]
            extra = max(0, by_source_copies.get(source, 0) - first)
            if first or extra:
                source_rows.append((source, (first, extra), f"{first} first · {extra} {'copy' if extra == 1 else 'copies'}",
                                    f"{source}: first to report {first} of {newsworthy} reports "
                                    f"({percent(first, newsworthy)}); added {extra} duplicate {'copy' if extra == 1 else 'copies'}"))
        self.sources.set_rows(source_rows, ("First reported", "Duplicate copies"))

        stations = Counter(report["sender"] for report in reports if report["sender"])
        self.stations.set_rows(
            [(station, (count,), f"{percent(count, newsworthy)} · {count}", f"{station}: first copy of {count} reports")
             for station, count in stations.most_common(10)])
