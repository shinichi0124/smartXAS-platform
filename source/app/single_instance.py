"""Single-instance enforcement for the smartLarch Platform GUI.

The first GUI process listens on a QLocalServer. A second launch connects,
asks the running instance to surface its window (restoring it from the tray
when needed) and exits immediately. CLI modes (``--validate`` / ``--cli-fit``)
never touch this module.

Raise handshake (v0.35.18): a raise request must be ANSWERED.
- ready window    → MainWindow replies ``ok`` after surfacing itself
- still starting  → the server itself replies ``boot`` (no window exists yet)
- no reply at all → the listener is stuck (e.g. a dead-import zombie holding
                    the pipe name)

Before this, a stuck pre-window instance silently swallowed every relaunch:
each new process connected, sent "raise", got nothing back and exited — the
app "could not be opened" until the zombie was killed by hand. Now a listener
that stays in boot past the grace period (or never replies) triggers a
takeover prompt that closes the unresponsive instance and starts fresh.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional, Tuple, Union

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "smartLarchPlatform.singleInstance"
_TIMEOUT_MS = 400
_REPLY_TIMEOUT_MS = 2500
_BOOT_GRACE_S = 20.0

_REPLY_OK = b"ok"
_REPLY_BOOT = b"boot"

# activate_existing() results
RAISED = True        # a ready instance answered and is surfacing its window
BOOTING = "booting"  # an instance is alive but still starting (no window yet)
STUCK = "stuck"      # a listener holds the name but never answers
NO_LISTENER = False  # nobody is listening


def activate_existing(
    timeout_ms: int = _TIMEOUT_MS,
    reply_timeout_ms: int = _REPLY_TIMEOUT_MS,
) -> Union[bool, str]:
    """Ask a running instance to come to the front.

    Returns ``True`` when a ready instance answered (it is surfacing its
    window), ``"booting"`` when an instance is alive but has no window yet,
    ``"stuck"`` when a listener holds the name but never answers, and
    ``False`` when nobody is listening.
    """
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        sock.abort()
        return False
    sock.write(b"raise")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    # Wait for the handshake reply on a nested event loop: waitForReadyRead
    # misses already-delivered data on Windows (documented Qt quirk — measured
    # here: the ack never surfaced while the client sat in waitForReadyRead).
    # The loop also lets an in-process peer (tests) answer.
    loop = QEventLoop()
    sock.readyRead.connect(loop.quit)
    timer = QTimer(sock)
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(max(reply_timeout_ms, 0))
    loop.exec()
    timer.stop()
    if sock.bytesAvailable():
        reply = bytes(sock.readAll())
        sock.disconnectFromServer()
        return True if reply.startswith(_REPLY_OK) else BOOTING
    sock.abort()
    return STUCK


class SingleInstanceServer(QObject):
    """Listens for raise-requests from later launches for the app's lifetime."""

    # Carries the peer socket; the main window writes the "ok" ack on it.
    raise_requested = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        # Flipped by run_app once the main window exists. Until then raise
        # requests are answered "boot" straight from here.
        self.ready = False

    def listen(self) -> bool:
        return bool(self._server.listen(SERVER_NAME))

    def close(self) -> None:
        self._server.close()

    def _on_new_connection(self) -> None:
        # A completed connection IS the raise request (only one message type
        # exists), so do not wait for readyRead — the payload may already be
        # drained by the time we get here if the peer disconnected fast.
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            conn.readAll()
            if conn.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                continue
            if self.ready:
                # MainWindow._on_second_launch(conn) surfaces the window and
                # writes the "ok" ack on this socket.
                self.raise_requested.emit(conn)
            else:
                try:
                    conn.write(_REPLY_BOOT)
                    conn.flush()
                    conn.waitForBytesWritten(_TIMEOUT_MS)
                except Exception:
                    pass
                conn.disconnectFromServer()


def _kill_peer_instances() -> None:
    """Force-stop other smartLarch GUI processes (zombie takeover).

    CLI modes (``--validate`` / ``--cli-fit``) are excluded — only windowed
    ``-m app.main`` processes are matched. Never kills the caller itself.
    """
    me = str(os.getpid())
    try:
        if sys.platform == "win32":
            ps = (
                "Get-CimInstance Win32_Process "
                "-Filter \"Name = 'pythonw.exe' OR Name = 'python.exe'\""
                " | Where-Object { $_.ProcessId -ne " + me +
                " -and $_.CommandLine -match 'app\\.main'"
                " -and $_.CommandLine -notmatch '--validate|--cli-fit' }"
                " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force"
                " -ErrorAction SilentlyContinue }"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                timeout=20,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.run(
                ["/bin/sh", "-c", "pgrep -f 'app\\.main' | grep -vx " + me + " | xargs kill -9"],
                timeout=10,
                capture_output=True,
            )
    except Exception:
        pass


def _confirm_takeover(language: str) -> bool:
    """Ask before closing an unresponsive instance. Headless/offscreen runs
    (tests, CI) auto-confirm: no user can read a dialog there."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get(
        "SMARTLARCH_TEST_SETTINGS"
    ):
        return True
    from PySide6.QtWidgets import QMessageBox

    zh = str(language or "").lower().startswith("zh")
    text = (
        "检测到上一个 smartLarch Platform 实例无响应（可能没有正常退出）。\n\n"
        "结束它并重新启动吗？（选「否」放弃本次启动）"
        if zh
        else "The previous smartLarch Platform instance is not responding "
        "(it may not have exited cleanly).\n\n"
        "Close it and start fresh? (Choose No to cancel this launch.)"
    )
    answer = QMessageBox.question(
        None,
        "smartLarch Platform",
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    return answer == QMessageBox.StandardButton.Yes


def ensure_single_instance(
    language: str = "zh",
    boot_grace_s: float = _BOOT_GRACE_S,
) -> Tuple[Optional[SingleInstanceServer], bool]:
    """Returns ``(server, should_exit)``.

    The caller must keep ``server`` alive for the whole app lifetime. When the
    single-instance machinery is unusable (extreme case), startup is never
    blocked: ``(None, False)`` is returned.
    """
    status = activate_existing()
    if status is True:
        return None, True
    if status in (BOOTING, STUCK):
        if status == BOOTING:
            # A healthy cold start (larch import) can take a while — give the
            # first instance a grace period before judging it unresponsive.
            deadline = time.monotonic() + boot_grace_s
            while time.monotonic() < deadline:
                time.sleep(1.0)
                status = activate_existing()
                if status != BOOTING:
                    break
        if status is True:
            return None, True  # it finished booting and surfaced itself
        # The other instance holds the name but cannot show a window.
        if not _confirm_takeover(language):
            return None, True  # keep the old instance; exit quietly
        _kill_peer_instances()
        time.sleep(0.5)
    server = SingleInstanceServer()
    if server.listen():
        return server, False
    # Listen failed: either we lost a startup race (the peer is listening now)
    # or a stale socket file blocks the name (Unix crash leftover).
    if activate_existing() is True:
        return None, True
    QLocalServer.removeServer(SERVER_NAME)
    if server.listen():
        return server, False
    return None, False
