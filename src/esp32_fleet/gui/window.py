"""Tela de configuração, prévia e execução da frota."""

import shlex
import sys
from codecs import getincrementaldecoder
from collections.abc import Sequence
from contextlib import redirect_stderr
from dataclasses import dataclass
from io import StringIO
from secrets import randbits

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esp32_fleet.config.settings import SimulatorSettings, parse_settings
from esp32_fleet.domain.vitals import VitalsScenario

DEVICES_PER_PAGE = 100
_RESOURCE_WARNING_SIZE = 50

_STYLE = """
QWidget#workspace { background: #101922; color: #e7eef4; }
QFrame#panel { background: #17242f; border: 1px solid #334958; border-radius: 10px; }
QLabel { color: #e7eef4; background: transparent; }
QLabel#title { font-size: 26px; font-weight: 700; }
QLabel#sectionTitle { font-size: 16px; font-weight: 700; }
QLabel#muted { color: #a8becf; }
QLabel#validation, QLabel#warning { color: #fec97c; }
QLabel#runStatus {
    background: #293d4d; border-radius: 6px; padding: 8px 14px; color: #e7eef4;
    font-weight: 600;
}
QLabel#runStatus[tone="active"] { background: #174d48; color: #b9f5db; }
QLabel#runStatus[tone="error"] { background: #542d39; color: #ffdadf; }
QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
    background: #101a24; color: #e7eef4; border: 1px solid #3c5364; border-radius: 5px;
    selection-background-color: #23566a; selection-color: white;
}
QLineEdit, QComboBox { min-height: 28px; padding: 2px 8px; }
QComboBox QAbstractItemView {
    background: #17242f; color: #e7eef4; selection-background-color: #23566a;
}
QLineEdit:focus, QComboBox:focus, QTableWidget:focus, QPlainTextEdit:focus {
    border-color: #6edded;
}
QLineEdit:disabled, QComboBox:disabled { color: #a2b7c7; background: #1c2b37; }
QTableWidget { alternate-background-color: #162531; gridline-color: #273b49; }
QHeaderView::section {
    padding: 7px; border: 0; border-bottom: 1px solid #3c5364;
    background: #213744; color: #cfe0e9; font-weight: 600;
}
QPushButton {
    background: #273c4b; color: #edf7fb; border: 1px solid #435d6c;
    border-radius: 6px; padding: 7px 11px;
}
QScrollBar:vertical { background: #101a24; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #4b6677; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QPushButton:hover { background: #345163; }
QPushButton:focus { border-color: #7ee8ed; }
QPushButton:disabled { background: #202f3b; color: #879da9; border-color: #344654; }
QPushButton#startButton:enabled { background: #76ded7; color: #102926; font-weight: 700; }
"""


@dataclass(frozen=True, slots=True)
class FleetForm:
    """Valores digitados, traduzidos para as opções do parser da engine."""

    device_id: str = ""
    devices: str = "1"
    scenario: str = VitalsScenario.BASELINE.value
    seed: str = ""
    samples: str = ""
    interval: str = "2"
    broker: str = "127.0.0.1"
    port: str = "1883"

    def arguments(self) -> list[str]:
        """Gera os argumentos compartilhados pelo worker e pelo comando copiável."""
        args = [
            f"--devices={self.devices.strip()}",
            f"--scenario={self.scenario}",
            f"--interval={self.interval.strip().replace(',', '.')}",
            f"--broker={self.broker.strip()}",
            f"--port={self.port.strip()}",
            "--metrics",
        ]
        for option, value in (
            ("--device-id", self.device_id),
            ("--seed", self.seed),
            ("--samples", self.samples),
        ):
            if value.strip():
                args.append(f"{option}={value.strip()}")
        return args


def validate_arguments(args: Sequence[str]) -> SimulatorSettings:
    """Converte o erro do argparse em uma mensagem exibível pela interface."""
    diagnostics = StringIO()
    try:
        with redirect_stderr(diagnostics):
            return parse_settings(args)
    except SystemExit as exc:
        lines = diagnostics.getvalue().strip().splitlines()
        message = lines[-1].split(": error: ", 1)[-1] if lines else "Configuração inválida."
        raise ValueError(message) from exc


def preview_ids(settings: SimulatorSettings, page: int) -> list[str]:
    """Calcula somente os IDs da página atual usando a numeração da engine."""
    start = page * DEVICES_PER_PAGE
    stop = min(start + DEVICES_PER_PAGE, settings.devices)
    if settings.devices == 1:
        return [settings.device_id] if start == 0 else []
    return [
        f"{settings.device_id}-{number:0{settings.device_number_width}d}"
        for number in range(start + 1, stop + 1)
    ]


def cli_command(args: Sequence[str], *, powershell: bool) -> str:
    """Formata o comando copiável para PowerShell ou um shell POSIX."""
    command = ["uv", "run", "--locked", "python", "-m", "esp32_fleet"]
    if powershell:
        # Em PowerShell, aspas simples duplicadas preservam uma aspa literal.
        quoted = ["'" + option.replace("'", "''") + "'" for option in args]
        return " ".join([*command, *quoted])
    return shlex.join([*command, *args])


class FleetWindow(QMainWindow):
    """Controla uma execução supervisionada e sua prévia de dispositivos."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Controle da frota · ESP32 Fleet Simulator")
        self.resize(1000, 730)
        self.setMinimumSize(760, 650)

        self._settings: SimulatorSettings | None = None
        self._args: list[str] = []
        self._page = 0
        self._stopping = False
        self._timed_out = False
        self._closing = False
        self._decoder = getincrementaldecoder("utf-8")(errors="replace")

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.started.connect(self._worker_started)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._worker_finished)
        self._process.errorOccurred.connect(self._worker_error)

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.setInterval(10_000)
        self._stop_timer.timeout.connect(self._stop_timeout)
        self._build_window()
        self._refresh_configuration()

    def _build_window(self) -> None:
        workspace = QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        self.setStyleSheet(_STYLE)
        root = QVBoxLayout(workspace)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Controle da frota")
        title.setObjectName("title")
        header.addWidget(title, 1)
        self._status = QLabel()
        self._status.setObjectName("runStatus")
        self._status.setAccessibleName("Estado da execução local")
        header.addWidget(self._status)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._configuration_panel(), 0)
        body.addWidget(self._preview_panel(), 1)
        root.addLayout(body, 1)

        self._warning = QLabel(
            "Acima de 50 dispositivos, confira a capacidade da máquina e do broker."
        )
        self._warning.setObjectName("warning")
        self._warning.setWordWrap(True)
        root.addWidget(self._warning)

        self._validation = QLabel()
        self._validation.setObjectName("validation")
        self._validation.setWordWrap(True)
        root.addWidget(self._validation)

        actions = QHBoxLayout()
        self._start = QPushButton("&Iniciar ensaio")
        self._start.setObjectName("startButton")
        self._start.clicked.connect(self._start_run)
        actions.addWidget(self._start)
        self._stop = QPushButton("&Parar")
        self._stop.setObjectName("stopButton")
        self._stop.setEnabled(False)
        self._stop.clicked.connect(self._request_stop)
        actions.addWidget(self._stop)
        self._copy = QPushButton("&Copiar comando CLI")
        self._copy.setObjectName("copyButton")
        self._copy.clicked.connect(self._copy_command)
        actions.addWidget(self._copy)
        actions.addStretch()
        root.addLayout(actions)

        log_title = QLabel("Saída da execução")
        log_title.setObjectName("sectionTitle")
        root.addWidget(log_title)
        self._output = QPlainTextEdit()
        self._output.setObjectName("runOutput")
        self._output.setAccessibleName("Saída da execução")
        self._output.setReadOnly(True)
        self._output.setMaximumBlockCount(200)
        self._output.setMinimumHeight(94)
        self._output.setMaximumHeight(148)
        self._output.setPlaceholderText("Os logs e as métricas finais aparecem aqui.")
        root.addWidget(self._output)
        self._set_status("Pronto")

    def _configuration_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        self._configuration = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        title = QLabel("Configuração do ensaio")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)
        layout.addLayout(form)
        self._fields: dict[str, QLineEdit] = {}

        def field(name: str, label: str, value: str, placeholder: str = "") -> QLineEdit:
            widget = QLineEdit(value)
            widget.setObjectName(name)
            widget.setPlaceholderText(placeholder)
            widget.textChanged.connect(self._refresh_configuration)
            form.addRow(label, widget)
            self._fields[name] = widget
            return widget

        field("deviceId", "&Prefixo ou ID", "", "Automático")
        field("devices", "&Quantidade", "1")
        self._scenario = QComboBox()
        self._scenario.setObjectName("scenario")
        self._scenario.addItems([scenario.value for scenario in VitalsScenario])
        self._scenario.currentTextChanged.connect(self._refresh_configuration)
        form.addRow("&Cenário", self._scenario)
        seed = field("seed", "Se&mente", "", "Aleatória")
        generate = QPushButton("&Gerar semente")
        generate.clicked.connect(lambda: seed.setText(str(randbits(32))))
        form.addRow("", generate)
        field("samples", "A&mostras por dispositivo", "", "Contínuo")
        field("interval", "Inter&valo (s)", "2")
        field("broker", "&Broker", "127.0.0.1")
        field("port", "P&orta", "1883")
        layout.addStretch()
        return panel

    def _preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        heading = QHBoxLayout()
        title = QLabel("Dispositivos previstos")
        title.setObjectName("sectionTitle")
        heading.addWidget(title, 1)
        self._count = QLabel()
        self._count.setObjectName("muted")
        heading.addWidget(self._count)
        layout.addLayout(heading)

        self._table = QTableWidget(0, 2)
        self._table.setObjectName("devicePreview")
        self._table.setAccessibleName("Prévia dos identificadores da frota")
        self._table.setHorizontalHeaderLabels(["Posição", "Identificador MQTT"])
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(120)
        layout.addWidget(self._table, 1)

        paging = QHBoxLayout()
        self._previous = QPushButton("Anterior")
        self._previous.clicked.connect(lambda: self._change_page(-1))
        paging.addWidget(self._previous)
        self._page_label = QLabel()
        self._page_label.setObjectName("muted")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        paging.addWidget(self._page_label, 1)
        self._next = QPushButton("Próxima")
        self._next.clicked.connect(lambda: self._change_page(1))
        paging.addWidget(self._next)
        layout.addLayout(paging)
        return panel

    def _refresh_configuration(self) -> None:
        form = FleetForm(
            device_id=self._fields["deviceId"].text(),
            devices=self._fields["devices"].text(),
            scenario=self._scenario.currentText(),
            seed=self._fields["seed"].text(),
            samples=self._fields["samples"].text(),
            interval=self._fields["interval"].text(),
            broker=self._fields["broker"].text(),
            port=self._fields["port"].text(),
        )
        self._args = form.arguments()
        self._page = 0
        try:
            self._settings = validate_arguments(self._args)
            self._validation.clear()
        except ValueError as exc:
            self._settings = None
            self._validation.setText(str(exc))
        self._validation.setVisible(self._settings is None)
        self._warning.setVisible(
            self._settings is not None and self._settings.devices > _RESOURCE_WARNING_SIZE
        )
        self._set_actions_running(self._process.state() != QProcess.ProcessState.NotRunning)
        self._render_page()

    def _render_page(self) -> None:
        settings = self._settings
        devices = preview_ids(settings, self._page) if settings is not None else []
        self._table.setRowCount(len(devices))
        for row, device_id in enumerate(devices):
            position = self._page * DEVICES_PER_PAGE + row + 1
            self._table.setItem(row, 0, QTableWidgetItem(str(position)))
            self._table.setItem(row, 1, QTableWidgetItem(device_id))
        total = settings.devices if settings is not None else 0
        pages = (total + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE
        self._count.setText(f"Total: {total}")
        self._page_label.setText(f"Página {self._page + 1} de {pages}" if pages else "Sem prévia")
        self._previous.setEnabled(self._page > 0)
        self._next.setEnabled(self._page + 1 < pages)

    def _change_page(self, offset: int) -> None:
        self._page += offset
        self._render_page()

    def _set_status(self, text: str, tone: str = "default") -> None:
        self._status.setText(text)
        self._status.setProperty("tone", tone)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _set_actions_running(self, running: bool) -> None:
        self._configuration.setEnabled(not running)
        self._start.setEnabled(not running and self._settings is not None)
        self._stop.setEnabled(running and not self._stopping)
        self._copy.setEnabled(self._settings is not None)

    def _copy_command(self) -> None:
        if self._settings is not None:
            clipboard = QApplication.clipboard()
            clipboard.setText(cli_command(self._args, powershell=sys.platform == "win32"))
            self.statusBar().showMessage("Comando CLI copiado.", 3000)

    def _start_run(self) -> None:
        if self._settings is None or self._process.state() != QProcess.ProcessState.NotRunning:
            return
        self._stopping = False
        self._timed_out = False
        self._decoder.reset()
        self._output.clear()
        self._set_status("Iniciando…", "active")
        self._set_actions_running(True)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        self._process.setProcessEnvironment(environment)
        self._process.start(sys.executable, ["-u", "-m", "esp32_fleet.gui.worker", *self._args])

    def _worker_started(self) -> None:
        if self._stopping:
            self._process.closeWriteChannel()
        else:
            self._set_status("Em execução local", "active")

    def _read_output(self) -> None:
        output = self._process.readAllStandardOutput().data()
        self._append_output(self._decoder.decode(output))

    def _append_output(self, text: str) -> None:
        if text:
            self._output.moveCursor(QTextCursor.MoveOperation.End)
            self._output.insertPlainText(text)

    def _request_stop(self) -> None:
        if self._process.state() == QProcess.ProcessState.NotRunning or self._stopping:
            return
        self._stopping = True
        self._set_status("Encerrando…", "active")
        self._stop.setEnabled(False)
        self._stop_timer.start()
        if self._process.state() == QProcess.ProcessState.Running:
            self._process.closeWriteChannel()

    def _stop_timeout(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._timed_out = True
            self._process.kill()

    def _worker_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._append_output(self._process.errorString())
            self._complete("Falha ao iniciar", "error")

    def _worker_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._read_output()
        self._append_output(self._decoder.decode(b"", final=True))
        if self._timed_out:
            self._complete("Tempo de parada excedido", "error")
        elif exit_code != 0 or status != QProcess.ExitStatus.NormalExit:
            self._complete("Falha na execução", "error")
        elif self._stopping:
            self._complete("Parado")
        else:
            self._complete("Concluído")

    def _complete(self, text: str, tone: str = "default") -> None:
        self._stop_timer.stop()
        self._set_status(text, tone)
        self._set_actions_running(False)
        if self._closing:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Solicita a parada antes de fechar uma janela com ensaio ativo."""
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._closing = True
            self._request_stop()
            event.ignore()
        else:
            event.accept()
