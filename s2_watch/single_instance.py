import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def default_server_name() -> str:
    return f"s2-watch-{getpass.getuser()}"


class SingleInstance(QObject):
    """Allows one running S2 Watch per user; later launches ask it to come forward."""

    activation_requested = Signal()

    def __init__(self, server_name: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.server_name = server_name or default_server_name()
        self._server: QLocalServer | None = None

    def notify_running_instance(self, timeout_ms: int = 500) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(b"show\n")
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def listen(self) -> bool:
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connection)
        if self._server.listen(self.server_name):
            return True
        # A crashed instance can leave its socket file behind.
        QLocalServer.removeServer(self.server_name)
        return self._server.listen(self.server_name)

    def _accept_connection(self) -> None:
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            self.activation_requested.emit()
