import uuid

from PySide6.QtCore import QCoreApplication

from s2_watch.single_instance import SingleInstance


def _app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def _process_events_until(app: QCoreApplication, condition, attempts: int = 50) -> None:
    for _ in range(attempts):
        if condition():
            return
        app.processEvents()
        QCoreApplication.processEvents()
        import time
        time.sleep(0.01)


def test_second_launch_activates_first_instance() -> None:
    app = _app()
    name = f"s2-watch-test-{uuid.uuid4().hex}"
    first = SingleInstance(name)
    assert not first.notify_running_instance()
    assert first.listen()
    activations = []
    first.activation_requested.connect(lambda: activations.append(True))

    assert SingleInstance(name).notify_running_instance()
    _process_events_until(app, lambda: activations)
    assert activations == [True]


def test_listen_recovers_from_stale_socket() -> None:
    _app()
    name = f"s2-watch-test-{uuid.uuid4().hex}"
    crashed = SingleInstance(name)
    assert crashed.listen()
    crashed._server.close()

    assert not SingleInstance(name).notify_running_instance()
    assert SingleInstance(name).listen()
