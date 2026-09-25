import signal
import sys

from PySide6.QtWidgets import QApplication

from s2_watch.single_instance import SingleInstance
from s2_watch.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    instance = SingleInstance()
    if instance.notify_running_instance():
        return 0
    instance.listen()
    window = MainWindow()
    instance.activation_requested.connect(window.show_from_tray)
    # Logout, shutdown, or kill: shut down the same way as closing the window.
    # The window's one-second poll timer gives Python a chance to run the handler.
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, lambda *_: window.close())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
