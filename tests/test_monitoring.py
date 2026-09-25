from pathlib import Path
import json
import socket
import threading
import time

from s2_watch.adapters.js8call_tcp import JS8CallTcpAdapter
from s2_watch.adapters.magcommstat_sqlite import MagCommstatSQLiteAdapter
from s2_watch.adapters.commstat_sqlite import CommstatSQLiteAdapter
from s2_watch.adapters.flmsg_files import FLMSGFileAdapter
from s2_watch.adapters.js8superspotter_sqlite import JS8SuperSpotterSQLiteAdapter
from s2_watch.adapters.text_log import TextLogAdapter
from s2_watch.models import Message, WatchForm
from s2_watch.monitoring import Monitor
from s2_watch.storage import SQLiteStore
from s2_watch.forms_io import forms_from_json, forms_to_json


def test_text_log_adapter_reads_only_new_lines(tmp_path: Path) -> None:
    log = tmp_path / "js8.log"
    log.write_text("first\n")
    adapter = TextLogAdapter(log)
    assert [m.raw_content for m in adapter.read_messages()] == ["first"]
    log.write_text("first\nsecond\n")
    assert [m.raw_content for m in adapter.read_messages()] == ["second"]


def test_monitor_stores_and_emits_matching_message(tmp_path: Path) -> None:
    class FakeAdapter:
        def read_messages(self):
            return [Message("JS8Call", "Emergency report")]

    matched = []
    monitor = Monitor(
        [FakeAdapter()],
        [WatchForm("JS8Call", "Emergency", "emergency")],
        SQLiteStore(tmp_path / "messages.db"),
        matched.append,
    )
    monitor.poll()
    assert matched[0].form_name == "Emergency"


def test_monitor_does_not_emit_messages_without_an_active_match(tmp_path: Path) -> None:
    class FakeAdapter:
        def read_messages(self):
            return [Message("JS8Call", "Routine status")]

    matched = []
    monitor = Monitor(
        [FakeAdapter()],
        [WatchForm("JS8Call", "Emergency", "emergency")],
        SQLiteStore(tmp_path / "unmatched.db"),
        matched.append,
    )
    monitor.poll()
    assert matched == []


def test_monitor_does_not_poll_applications_without_enabled_forms(tmp_path: Path) -> None:
    class FakeAdapter:
        application_name = "MagCommstat"

        def __init__(self):
            self.read_count = 0

        def read_messages(self):
            self.read_count += 1
            return [Message("MagCommstat", "Historical report")]

    adapter = FakeAdapter()
    monitor = Monitor(
        [adapter],
        [WatchForm("JS8Call", "All JS8Call messages", "", enabled=False)],
        SQLiteStore(tmp_path / "not-consumed.db"),
        lambda _: None,
    )
    monitor.poll()
    assert adapter.read_count == 0


def test_blank_watch_form_text_matches_all_messages() -> None:
    form = WatchForm("JS8Call", "All messages", "")
    assert form.matches(Message("JS8Call", "Any report"))
    assert not form.matches(Message("Commstat", "Any report"))


def test_monitor_persists_analysis_result(tmp_path: Path) -> None:
    from s2_watch.analysis.rules import RuleBasedNewsAnalyzer

    class FakeAdapter:
        def read_messages(self):
            return [Message("JS8Call", "Flood evacuation report")]

    store = SQLiteStore(tmp_path / "analysis.db")
    monitor = Monitor(
        [FakeAdapter()],
        [WatchForm("JS8Call", "Emergency", "flood")],
        store,
        lambda _: None,
        RuleBasedNewsAnalyzer(),
        lambda *_: None,
    )
    monitor.poll()
    assert store.count_analysis_results() == 1
    store.close()


def test_js8call_tcp_adapter_reads_json_line() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def send_message() -> None:
        connection, _ = server.accept()
        connection.sendall((json.dumps({"type": "RX.DIRECTED", "text": "Emergency"}) + "\n").encode())
        connection.close()

    thread = threading.Thread(target=send_message)
    thread.start()
    adapter = JS8CallTcpAdapter(port=port)
    assert adapter.read_messages() == []
    time.sleep(0.05)
    messages = adapter.read_messages()
    thread.join()
    server.close()
    assert messages[0].raw_content == "Emergency"


def test_watch_forms_are_persisted(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "forms.db")
    form = WatchForm("JS8Call", "Emergency", "emergency")
    store.save_watch_form(form)
    assert store.list_watch_forms("JS8Call") == [form]
    store.save_watch_form(WatchForm("JS8Call", "Emergency", "urgent", False))
    assert store.list_watch_forms("JS8Call")[0].match_text == "urgent"
    store.delete_watch_form("JS8Call", "Emergency")
    assert store.list_watch_forms("JS8Call") == []
    store.close()


def test_all_watch_forms_can_be_disabled(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "disable-forms.db")
    store.save_watch_form(WatchForm("JS8Call", "All messages", "", enabled=True))
    store.save_watch_form(WatchForm("FLMSG", "Forms", "", enabled=True))
    store.disable_all_watch_forms()
    assert all(not form.enabled for form in store.list_watch_forms())
    store.close()


def test_blank_watch_forms_match_each_application() -> None:
    applications = ("JS8Call", "MagCommstat", "Commstat", "FLMSG", "JS8SuperSpotter")
    for application in applications:
        form = WatchForm(application, f"All {application}", "")
        assert form.matches(Message(application, "Any report"))


def test_watch_forms_can_round_trip_json() -> None:
    forms = [WatchForm("JS8Call", "Emergency", "flood", False, True, "RX", "N0ABC")]
    restored = forms_from_json(forms_to_json(forms))
    assert restored == forms


def test_watch_form_json_rejects_unknown_application() -> None:
    try:
        forms_from_json('{"watch_forms": [{"application": "Unknown", "name": "bad"}]}')
    except ValueError as error:
        assert "unsupported application" in str(error)
    else:
        raise AssertionError("Expected invalid application to be rejected")


def test_application_version_is_defined() -> None:
    from s2_watch.ui.main_window import APP_VERSION

    assert APP_VERSION == "0.1.0"


def test_settings_are_persisted(tmp_path: Path) -> None:
    path = tmp_path / "settings.db"
    store = SQLiteStore(path)
    assert store.get_setting("analysis.mode", "rules") == "rules"
    store.set_setting("analysis.mode", "ollama")
    store.close()
    reopened = SQLiteStore(path)
    assert reopened.get_setting("analysis.mode") == "ollama"
    reopened.close()


def test_history_search_filters_analysis(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "history.db")
    message_id = store.save_message(Message("JS8Call", "Flood evacuation"))
    from s2_watch.analysis.base import AnalysisResult

    store.save_analysis(
        message_id,
        AnalysisResult(True, "severe_weather", "high", "Flood", 0.8, ("flood",)),
    )
    assert len(store.search_history(newsworthy_only=True, urgency="high")) == 1
    assert len(store.search_history(category="medical")) == 0
    store.close()


def test_alert_settings_are_persisted(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "alerts.db")
    store.set_setting("alerts.enabled", "0")
    store.set_setting("alerts.high_urgency_only", "1")
    assert store.get_setting("alerts.enabled") == "0"
    assert store.get_setting("alerts.high_urgency_only") == "1"
    store.close()


def test_magcommstat_sqlite_adapter_reads_new_messages(tmp_path: Path) -> None:
    database = tmp_path / "traffic.db3"
    connection = __import__("sqlite3").connect(database)
    connection.execute(
        """CREATE TABLE messages (
            id INTEGER PRIMARY KEY, utc TEXT, from_call TEXT, to_call TEXT,
            body TEXT, group_name TEXT, rig_name TEXT, msg_id TEXT
        )"""
    )
    connection.execute(
        "INSERT INTO messages(id, from_call, to_call, body, group_name, rig_name, msg_id) "
        "VALUES (1, 'N0CALL', 'W1AW', 'Flood report', 'NET', 'IC-705', 'abc')"
    )
    connection.commit()
    connection.close()
    adapter = MagCommstatSQLiteAdapter(database)
    messages = adapter.read_messages()
    assert messages[0].source_application == "MagCommstat"
    assert messages[0].raw_content == "Flood report"
    assert messages[0].fields["sender"] == "N0CALL"
    assert adapter.read_messages() == []


def test_magcommstat_adapter_reads_alerts_and_statrep(tmp_path: Path) -> None:
    database = tmp_path / "traffic.db3"
    connection = __import__("sqlite3").connect(database)
    connection.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, utc TEXT, from_call TEXT, to_call TEXT, title TEXT, message TEXT, color INTEGER, alert_id TEXT)")
    connection.execute("CREATE TABLE statrep (id INTEGER PRIMARY KEY, utc TEXT, from_call TEXT, to_call TEXT, remarks TEXT, grid TEXT, scope TEXT, msg_id TEXT)")
    connection.execute("INSERT INTO alerts VALUES (1, '', 'N0CALL', '', 'Fire', 'Warehouse fire', 3, 'a1')")
    connection.execute("INSERT INTO statrep VALUES (1, '', 'W1AW', '', 'Road closed', 'FN20', 'My Region', 's1')")
    connection.commit()
    connection.close()
    messages = MagCommstatSQLiteAdapter(database).read_messages()
    assert {message.fields["type"] for message in messages} == {"ALERTS", "STATREP"}
    assert any("Warehouse fire" in message.raw_content for message in messages)


def test_commstat_sqlite_adapter_reads_legacy_schema(tmp_path: Path) -> None:
    database = tmp_path / "traffic.db3"
    connection = __import__("sqlite3").connect(database)
    connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, datetime TEXT, from_callsign TEXT, target TEXT, message TEXT, msg_id TEXT)")
    connection.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, datetime TEXT, from_callsign TEXT, target TEXT, title TEXT, message TEXT, color INTEGER, alert_id TEXT)")
    connection.execute("CREATE TABLE statrep (id INTEGER PRIMARY KEY, datetime TEXT, from_callsign TEXT, target TEXT, comments TEXT, grid TEXT, scope TEXT, sr_id TEXT)")
    connection.execute("INSERT INTO messages VALUES (1, '', 'N0CALL', 'W1AW', 'Flood report', 'm1')")
    connection.execute("INSERT INTO alerts VALUES (1, '', 'N0CALL', '', 'Fire', 'Warehouse fire', 3, 'a1')")
    connection.execute("INSERT INTO statrep VALUES (1, '', 'W1AW', '', 'Road closed', 'FN20', 'Region', 's1')")
    connection.commit()
    connection.close()
    messages = CommstatSQLiteAdapter(database).read_messages()
    assert len(messages) == 3
    assert all(message.source_application == "Commstat" for message in messages)


def test_flmsg_file_adapter_reads_new_message_files(tmp_path: Path) -> None:
    message_file = tmp_path / "report.k2s"
    message_file.write_text(
        "<flmsg>4.0.23\n:hdr_fm:12 \nN0CALL 20260101000000\n"
        ":mg:40 CUSTOM_FORM,emergency.html\nFire near town\n"
    )
    adapter = FLMSGFileAdapter(tmp_path)
    messages = adapter.read_messages()
    assert messages[0].source_application == "FLMSG"
    assert messages[0].fields["form"] == "emergency.html"
    assert messages[0].fields["sender"] == "N0CALL"
    assert "Fire near town" in messages[0].raw_content
    assert adapter.read_messages() == []


def test_js8superspotter_adapter_reads_structured_tables(tmp_path: Path) -> None:
    database = tmp_path / "js8spotter.db"
    connection = __import__("sqlite3").connect(database)
    connection.execute("CREATE TABLE activity (id INTEGER PRIMARY KEY, type TEXT, value TEXT, call TEXT, snr INTEGER, speed INTEGER)")
    connection.execute("CREATE TABLE csstatrep (id INTEGER PRIMARY KEY, cssr_from TEXT, cssr_group TEXT, cssr_grid TEXT, cssr_prio INTEGER, cssr_status TEXT, cssr_notes TEXT)")
    connection.execute("INSERT INTO activity VALUES (1, 'RX', 'Flood report', 'N0CALL', -10, 0)")
    connection.execute("INSERT INTO csstatrep VALUES (1, 'W1AW', 'NET', 'FN20', 1, 'RED', 'Road closed')")
    connection.commit()
    connection.close()
    messages = JS8SuperSpotterSQLiteAdapter(database).read_messages()
    assert len(messages) == 2
    assert all(message.source_application == "JS8SuperSpotter" for message in messages)
    assert any(message.fields["type"] == "CSSTATREP" for message in messages)


def test_watch_form_can_match_type_and_sender() -> None:
    form = WatchForm("JS8Call", "Directed emergency", "flood", message_type="RX.DIRECTED", sender="N0CALL")
    matching = Message("JS8Call", "Flood report", fields={"type": "RX.DIRECTED", "sender": "N0CALL"})
    wrong_type = Message("JS8Call", "Flood report", fields={"type": "RX.DIRECT", "sender": "N0CALL"})
    wrong_sender = Message("JS8Call", "Flood report", fields={"type": "RX.DIRECTED", "sender": "K0CALL"})
    assert form.matches(matching)
    assert not form.matches(wrong_type)
    assert not form.matches(wrong_sender)


def test_all_applications_form_matches_and_activates_every_source(tmp_path: Path) -> None:
    from s2_watch.models import ANY_APPLICATION

    class Source:
        def __init__(self, name, text):
            self.application_name, self.text, self.reads = name, text, 0

        def read_messages(self):
            self.reads += 1
            return [Message(self.application_name, self.text)] if self.reads == 1 else []

    sources = [Source("JS8Call", "PROTEST DOWNTOWN"), Source("FLMSG", "ROAD CLOSED"), Source("Commstat", "PROTEST AT CAPITOL")]
    form = WatchForm(ANY_APPLICATION, "Civil Unrest", r"\bprotest", use_regex=True)
    matched = []
    Monitor(sources, [form], SQLiteStore(tmp_path / "s2.db"), matched.append).poll()
    assert all(source.reads == 1 for source in sources)
    assert sorted(m.source_application for m in matched) == ["Commstat", "JS8Call"]
    assert all(m.form_name == "Civil Unrest" for m in matched)


def test_rescan_shows_messages_again_without_saving_duplicates(tmp_path: Path) -> None:
    class Source:
        application_name = "FLMSG"

        def read_messages(self):
            return [Message("FLMSG", "FLOODING ON MAIN ST", fields={"filename": "a.k2s"})]

    store = SQLiteStore(tmp_path / "s2.db")
    shown = []
    form = WatchForm("FLMSG", "Weather", "flood")
    for _ in range(2):  # the second pass is a re-scan (or a restart) with fresh adapters
        Monitor([Source()], [form], store, shown.append).poll()
    assert len(shown) == 2
    assert len(store.search_history(text="FLOODING")) == 1
