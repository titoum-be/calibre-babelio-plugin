"""Configuration UI and persistent preferences for the Babelio plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from calibre.utils.config import JSONConfig
from qt.core import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    Qt,
    QThread,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

from .client import _DEFAULT_USER_AGENT, ConnectionResult, ConnectionStatus
from .errors import (
    circuit_open_message,
    cookie_expired_message,
    login_failed_message,
    token_missing_message,
)
from .worker import WorkerConfig

if TYPE_CHECKING:

    def _(text: str) -> str: ...


prefs = JSONConfig("plugins/babelio_custom")
prefs.defaults["username"] = ""
prefs.defaults["password"] = ""
prefs.defaults["jsts_token"] = ""
prefs.defaults["user_agent"] = _DEFAULT_USER_AGENT
prefs.defaults["min_interval"] = 1.2
prefs.defaults["search_interval"] = 3.0
prefs.defaults["max_results"] = 5
prefs.defaults["comments"] = True
prefs.defaults["pubdate"] = True
prefs.defaults["publisher"] = True
prefs.defaults["rating"] = True
prefs.defaults["series"] = True
prefs.defaults["tags"] = True
prefs.defaults["verbosity"] = 5
prefs.defaults["allow_covers"] = True


def worker_config_from_prefs() -> WorkerConfig:
    return WorkerConfig(
        comments=prefs["comments"],
        pubdate=prefs["pubdate"],
        publisher=prefs["publisher"],
        rating=prefs["rating"],
        series=prefs["series"],
        tags=prefs["tags"],
    )


class _TestConnectionWorker(QThread):  # type: ignore[misc]
    """Runs the Babelio connection test off the GUI thread; emits the `ConnectionResult`."""

    result_ready = pyqtSignal(object)

    def __init__(
        self,
        username: str,
        password: str,
        cookie: str,
        user_agent: str,
        min_interval: float,
        search_interval: float,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._username = username
        self._password = password
        self._cookie = cookie
        self._user_agent = user_agent
        self._min_interval = min_interval
        self._search_interval = search_interval

    def run(self) -> None:
        from calibre import browser

        from ._browser import CalibreBrowserAdapter
        from .client import BabelioClient

        try:
            client = BabelioClient(
                CalibreBrowserAdapter(browser()),
                self._cookie,
                self._user_agent,
                username=self._username,
                password=self._password,
                min_interval=self._min_interval,
                search_interval=self._search_interval,
            )
            result = client.test_connection()
        except Exception as exc:  # noqa: BLE001 - UI worker must never raise across the thread.
            result = ConnectionResult(ConnectionStatus.ERROR, str(exc))
        self.result_ready.emit(result)


class ConfigWidget(QWidget):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_fields_group())
        layout.addWidget(self._build_options_group())
        layout.addStretch(1)
        self._load_from_prefs()

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox(_("Babelio connection"), self)
        outer = QVBoxLayout(group)

        help_label = QLabel(
            _(
                "Enter your Babelio credentials (optional). If Babelio challenges your connection "
                "with HTTP 403, you can also paste the <b>jstsToken</b> cookie from your browser:"
            )
            + "<ol>"
            + "<li>"
            + _("Open www.babelio.com in your browser and log in.")
            + "</li><li>"
            + _(
                "Open DevTools (F12) → <b>Application</b> tab → <b>Storage ▸ Cookies</b> → "
                "<code>https://www.babelio.com</code>."
            )
            + "</li><li>"
            + _("Find the <b>jstsToken</b> row and copy its <b>Value</b>.")
            + "</li><li>"
            + _("Paste it below.")
            + "</li></ol>"
            + _(
                "<i>This token expires after about 3 weeks; when imports are blocked with 403, "
                "repeat these steps to paste a fresh one.</i>"
            ),
            group,
        )
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        help_label.setOpenExternalLinks(True)
        outer.addWidget(help_label)

        form = QFormLayout()
        outer.addLayout(form)

        self.username = QLineEdit(group)
        self.username.setPlaceholderText(_("Username or email (optional)"))
        self.username.setToolTip(_("Your Babelio login username or email address."))
        form.addRow(_("Babelio username:"), self.username)

        self.password = QLineEdit(group)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(_("Password (optional)"))
        self.password.setToolTip(_("Your Babelio account password."))
        form.addRow(_("Babelio password:"), self.password)

        self.jsts_token = QLineEdit(group)
        self.jsts_token.setPlaceholderText(_("Paste the jstsToken value here (optional)"))
        self.jsts_token.setToolTip(
            _(
                "Optional anti-bot cookie needed only if Babelio blocks requests with HTTP 403. "
                "Expires after about 3 weeks."
            )
        )
        form.addRow(_("Babelio cookie (jstsToken):"), self.jsts_token)

        self.user_agent = QLineEdit(group)
        self.user_agent.setPlaceholderText(_DEFAULT_USER_AGENT)
        self.user_agent.setToolTip(
            _(
                "Sent with every request; leave as-is unless requests are being blocked. Use the "
                "same browser you copied the cookie from for best results."
            )
        )
        form.addRow(_("Browser User-Agent (optional):"), self.user_agent)

        self.min_interval = QDoubleSpinBox(group)
        self.min_interval.setRange(0.5, 60.0)
        self.min_interval.setSingleStep(0.1)
        self.min_interval.setSuffix(" s")
        self.min_interval.setToolTip(
            _(
                "Minimum pause between requests to Babelio. Higher is slower but safer against "
                "rate-limiting."
            )
        )
        form.addRow(_("Minimum delay between requests:"), self.min_interval)

        self.search_interval = QDoubleSpinBox(group)
        self.search_interval.setRange(0.0, 120.0)
        self.search_interval.setSingleStep(0.5)
        self.search_interval.setSuffix(" s")
        self.search_interval.setToolTip(
            _("Extra minimum delay between search requests (helps avoid anti-bot triggers).")
        )
        form.addRow(_("Minimum delay between searches:"), self.search_interval)

        test_row = QHBoxLayout()
        self.test_button = QPushButton(_("Test connection"), group)
        self.test_button.clicked.connect(self._on_test_connection)
        test_row.addWidget(self.test_button)
        self.test_status = QLabel("", group)
        self.test_status.setTextFormat(Qt.TextFormat.RichText)
        test_row.addWidget(self.test_status, 1)
        outer.addLayout(test_row)

        return group

    def _build_fields_group(self) -> QGroupBox:
        group = QGroupBox(_("Metadata to import"), self)
        outer = QVBoxLayout(group)
        outer.addWidget(QLabel(_("Choose which fields to fetch from Babelio."), group))

        grid = QGridLayout()
        outer.addLayout(grid)
        self.comments = QCheckBox(_("Comments"), group)
        self.pubdate = QCheckBox(_("Published date"), group)
        self.publisher = QCheckBox(_("Publisher"), group)
        self.rating = QCheckBox(_("Rating"), group)
        self.series = QCheckBox(_("Series"), group)
        self.tags = QCheckBox(_("Tags"), group)
        for index, box in enumerate(
            (self.comments, self.pubdate, self.publisher, self.rating, self.series, self.tags)
        ):
            grid.addWidget(box, index // 2, index % 2)

        return group

    def _build_options_group(self) -> QGroupBox:
        group = QGroupBox(_("Advanced options"), self)
        outer = QVBoxLayout(group)
        form = QFormLayout()
        outer.addLayout(form)

        self.max_results = QSpinBox(group)
        self.max_results.setRange(1, 20)
        self.max_results.setToolTip(
            _("How many search hits to look up; higher finds more but is slower and riskier.")
        )
        form.addRow(_("Max results:"), self.max_results)

        self.allow_covers = QCheckBox(_("Allow cover download"), group)
        form.addRow(self.allow_covers)

        self.verbosity = QSpinBox(group)
        self.verbosity.setRange(0, 15)
        self.verbosity.setToolTip(_("Higher = more diagnostic logging."))
        form.addRow(_("Verbosity:"), self.verbosity)

        return group

    def _load_from_prefs(self) -> None:
        self.username.setText(prefs["username"])
        self.password.setText(prefs["password"])
        self.jsts_token.setText(prefs["jsts_token"])
        self.user_agent.setText(prefs["user_agent"])
        self.min_interval.setValue(prefs["min_interval"])
        self.search_interval.setValue(prefs["search_interval"])
        self.max_results.setValue(prefs["max_results"])
        self.comments.setChecked(prefs["comments"])
        self.pubdate.setChecked(prefs["pubdate"])
        self.publisher.setChecked(prefs["publisher"])
        self.rating.setChecked(prefs["rating"])
        self.series.setChecked(prefs["series"])
        self.tags.setChecked(prefs["tags"])
        self.verbosity.setValue(prefs["verbosity"])
        self.allow_covers.setChecked(prefs["allow_covers"])

    def _on_test_connection(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        cookie = self.jsts_token.text().strip()
        user_agent = self.user_agent.text().strip() or _DEFAULT_USER_AGENT
        if bool(username) != bool(password):
            QMessageBox.warning(
                self,
                _("Test connection"),
                _("Enter both username and password, or leave both empty."),
            )
            return

        self.test_button.setEnabled(False)
        self.test_status.setText(_("Testing…"))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        self._test_worker = _TestConnectionWorker(
            username,
            password,
            cookie,
            user_agent,
            self.min_interval.value(),
            self.search_interval.value(),
            self,
        )
        self._test_worker.result_ready.connect(self._on_test_finished)
        self._test_worker.finished.connect(self._test_worker.deleteLater)
        self._test_worker.start()

    def _on_test_finished(self, result: ConnectionResult) -> None:
        QApplication.restoreOverrideCursor()
        self.test_button.setEnabled(True)

        if result.status is ConnectionStatus.OK:
            ok_text = _("Connection OK")
            self.test_status.setText(f"<span style='color: green;'>✅ {ok_text}</span>")
            QMessageBox.information(self, _("Test connection"), ok_text)
            return

        message = self._connection_error_message(result.status, result.detail)
        self.test_status.setText(f"<span style='color: red;'>❌ {message}</span>")
        QMessageBox.warning(self, _("Test connection"), message)

    @staticmethod
    def _connection_error_message(status: ConnectionStatus, detail: str) -> str:
        if status is ConnectionStatus.AUTH_FAILED:
            return login_failed_message()
        if status is ConnectionStatus.TOKEN_MISSING:
            return token_missing_message()
        if status is ConnectionStatus.TOKEN_EXPIRED:
            return cookie_expired_message()
        if status is ConnectionStatus.CIRCUIT_OPEN:
            return circuit_open_message()
        return _("Connection failed: {error}").format(error=detail)

    def save_settings(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        cookie = self.jsts_token.text().strip()
        user_agent = self.user_agent.text().strip()

        if bool(username) != bool(password):
            QMessageBox.warning(
                self,
                _("Babelio settings"),
                _("Enter both username and password, or leave both empty."),
            )
        elif not user_agent:
            QMessageBox.warning(
                self,
                _("Babelio settings"),
                _("A Browser User-Agent is required for imports to work."),
            )

        prefs["username"] = username
        prefs["password"] = password
        prefs["jsts_token"] = cookie
        prefs["user_agent"] = user_agent
        prefs["min_interval"] = self.min_interval.value()
        prefs["search_interval"] = self.search_interval.value()
        prefs["max_results"] = self.max_results.value()
        prefs["comments"] = self.comments.isChecked()
        prefs["pubdate"] = self.pubdate.isChecked()
        prefs["publisher"] = self.publisher.isChecked()
        prefs["rating"] = self.rating.isChecked()
        prefs["series"] = self.series.isChecked()
        prefs["tags"] = self.tags.isChecked()
        prefs["verbosity"] = self.verbosity.value()
        prefs["allow_covers"] = self.allow_covers.isChecked()
