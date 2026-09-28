import argparse
import os
import signal
import sys

from PySide6.QtWidgets import QApplication

from s2_watch.single_instance import SingleInstance
from s2_watch.ui.icons import app_icon
from s2_watch.ui.main_window import MainWindow


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="s2-watch", description="S2 Watch radio report monitor")
    parser.add_argument(
        "--database", default=os.environ.get("S2WATCH_DATABASE", "s2_watch.db"),
        help="settings and history database (default: s2_watch.db in the current folder, "
             "or $S2WATCH_DATABASE); the installers point this at a per-user data folder",
    )
    parser.add_argument("--configure", action="store_true", help="run the setup wizard in this terminal, then exit")
    arguments, _qt_arguments = parser.parse_known_args(argv[1:])
    return arguments


def main() -> int:
    arguments = parse_arguments(sys.argv)
    if arguments.configure:
        from s2_watch.setup_wizard import run_wizard
        return run_wizard(arguments.database)
    app = QApplication(sys.argv)
    app.setApplicationName("s2-watch")
    app.setWindowIcon(app_icon())
    app.setDesktopFileName("s2-watch")
    instance = SingleInstance()
    if instance.notify_running_instance():
        return 0
    instance.listen()
    window = MainWindow(arguments.database)
    instance.activation_requested.connect(window.show_from_tray)
    # Logout, shutdown, or kill: shut down the same way as closing the window.
    # The window's one-second poll timer gives Python a chance to run the handler.
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, lambda *_: window.close())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
