"""Exercita os filtros da tabela Qt sem depender do broker."""

from collections.abc import Callable, Iterator
from time import monotonic

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPushButton, QTableWidget

from esp32_fleet.gui import window as gui
from esp32_fleet.gui.window import FleetWindow


@pytest.fixture
def fleet_table(monkeypatch: pytest.MonkeyPatch) -> Iterator[FleetWindow]:
    """Abre uma tabela de 50 dispositivos e usa páginas pequenas nos testes."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    for name in ("USERNAME", "PASSWORD", "CA_FILE"):
        monkeypatch.delenv(f"ESP32_FLEET_MQTT_{name}", raising=False)
    monkeypatch.setattr(gui, "DEVICES_PER_PAGE", 10)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    window = FleetWindow()
    devices = window.findChild(QLineEdit, "devices")
    assert devices is not None
    devices.setText("50")
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def _device_rows(table: QTableWidget) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for row in range(table.rowCount()):
        position = table.item(row, 0)
        device = table.item(row, 1)
        state = table.item(row, 2)
        assert position is not None and device is not None and state is not None
        rows.append((position.text(), device.text(), state.text()))
    return rows


def _wait_for(condition: Callable[[], bool]) -> None:
    deadline = monotonic() + 3
    while not condition() and monotonic() < deadline:
        QTest.qWait(10)
    assert condition()


def test_id_search_filters_all_pages_and_retains_fleet_positions(fleet_table: FleetWindow) -> None:
    """Mostra a linha da última página ao buscar um trecho do ID."""
    table = fleet_table.findChild(QTableWidget, "devicePreview")
    search = fleet_table.findChild(QLineEdit, "deviceIdFilter")
    count = fleet_table.findChild(QLabel, "deviceCount")
    page = fleet_table.findChild(QLabel, "devicePage")
    next_page = fleet_table.findChild(QPushButton, "nextDevicePage")
    assert table is not None and search is not None and count is not None
    assert page is not None and next_page is not None
    assert table.rowCount() == 10 and count.text() == "Total: 50"

    next_page.click()
    assert page.text() == "Página 2 de 5"
    search.setText("  ESP32-050 ")
    assert _device_rows(table) == [("50", "esp32-050", "Aguardando")]
    assert count.text() == "1 de 50" and page.text() == "Página 1 de 1"
    assert not next_page.isEnabled()

    search.setText("ausente")
    assert table.rowCount() == 0 and page.text() == "Sem resultados"
    search.clear()
    assert table.rowCount() == 10 and next_page.isEnabled()


def test_combined_filters_follow_announcements_and_disconnect(
    fleet_table: FleetWindow,
) -> None:
    """Reflete mudanças de estado e volta a Aguardando quando perde o broker."""
    table = fleet_table.findChild(QTableWidget, "devicePreview")
    search = fleet_table.findChild(QLineEdit, "deviceIdFilter")
    state = fleet_table.findChild(QComboBox, "deviceStateFilter")
    assert table is not None and search is not None and state is not None

    search.setText("-04")
    state.setCurrentText("Online")
    assert table.rowCount() == 0
    fleet_table._receive_device_state("esp32-006", "online")
    fleet_table._receive_device_state("esp32-048", "online")
    _wait_for(lambda: _device_rows(table) == [("48", "esp32-048", "Online")])

    fleet_table._receive_device_state("esp32-048", "offline")
    _wait_for(lambda: table.rowCount() == 0)
    state.setCurrentText("Offline")
    assert _device_rows(table) == [("48", "esp32-048", "Offline")]

    fleet_table._monitor_connection_changed(False)
    _wait_for(lambda: table.rowCount() == 0)
    state.setCurrentText("Aguardando")
    assert _device_rows(table)[-1] == ("49", "esp32-049", "Aguardando")


def test_state_filter_clamps_last_page_after_result_shrinks(
    fleet_table: FleetWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recalcula em lotes e volta para a última página que ainda tem linhas."""
    monkeypatch.setattr(gui, "_FILTER_BATCH_SIZE", 10)
    state = fleet_table.findChild(QComboBox, "deviceStateFilter")
    table = fleet_table.findChild(QTableWidget, "devicePreview")
    page = fleet_table.findChild(QLabel, "devicePage")
    next_page = fleet_table.findChild(QPushButton, "nextDevicePage")
    assert state is not None and table is not None and page is not None and next_page is not None
    for position in range(1, 12):
        fleet_table._receive_device_state(f"esp32-{position:03d}", "online")
    state.setCurrentText("Online")
    _wait_for(next_page.isEnabled)
    next_page.click()
    _wait_for(lambda: _device_rows(table) == [("11", "esp32-011", "Online")])

    fleet_table._receive_device_state("esp32-011", "offline")
    _wait_for(lambda: page.text() == "Página 1 de 1")
    assert table.rowCount() == 10 and not next_page.isEnabled()
