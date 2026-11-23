"""Verifica o ciclo de execução da janela Qt contra Mosquitto em loopback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import aiomqtt
import pytest
from test_mqtt_cli import _message
from test_mqtt_connection_integration import _broker, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from esp32_fleet.gui.window import FleetWindow


@pytest.fixture
def fleet_window(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[QApplication, FleetWindow]]:
    """Cria a janela em modo offscreen e encerra o worker caso a verificação falhe."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from esp32_fleet.gui.window import FleetWindow

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    for name in ("USERNAME", "PASSWORD", "CA_FILE"):
        monkeypatch.delenv(f"ESP32_FLEET_MQTT_{name}", raising=False)
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    app.setQuitOnLastWindowClosed(False)
    window = FleetWindow()
    window.show()
    try:
        yield app, window
    finally:
        window.close()
        deadline = time.monotonic() + 11
        while window.isVisible() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        window.deleteLater()
        app.processEvents()


async def _pump_qt(app: QApplication) -> None:
    while True:
        app.processEvents()
        await asyncio.sleep(0.01)


async def _exercise_fleet(
    executable: str,
    port: int,
    config: Path,
    app: QApplication,
    window: FleetWindow,
    stop_manually: bool,
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

    inputs = {
        "devices": "3",
        "deviceId": "frota",
        "interval": "0.1",
        "port": str(port),
        "samples": "" if stop_manually else "2",
    }
    for name, value in inputs.items():
        field = window.findChild(QLineEdit, name)
        assert field is not None
        field.setText(value)
    start = window.findChild(QPushButton, "startButton")
    stop = window.findChild(QPushButton, "stopButton")
    status = window.findChild(QLabel, "runStatus")
    assert start is not None and stop is not None and status is not None
    assert start.isEnabled() and not stop.isEnabled()

    devices = {"frota-001", "frota-002", "frota-003"}
    online: dict[str, str] = {}
    samples: dict[str, list[int]] = {device_id: [] for device_id in devices}
    offline: set[str] = set()
    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2) as subscriber:
            await subscriber.subscribe("vitals/+/+", qos=1)
            async with asyncio.timeout(14), asyncio.TaskGroup() as tasks:
                pump = tasks.create_task(_pump_qt(app))
                QTest.mouseClick(start, Qt.MouseButton.LeftButton)
                while offline != devices:
                    topic, payload = await _message(subscriber)
                    _, device_id, channel = topic.split("/")
                    assert device_id in devices and payload["device_id"] == device_id
                    if channel == "data":
                        assert payload["boot_id"] == online[device_id]
                        samples[device_id].append(payload["sequence_number"])
                    elif channel == "status" and payload["state"] == "online":
                        online[device_id] = payload["boot_id"]
                    elif channel == "status":
                        assert payload["reason"] == "graceful_shutdown"
                        assert payload["boot_id"] == online[device_id]
                        offline.add(device_id)

                    if stop_manually and stop.isEnabled() and all(samples.values()):
                        QTest.mouseClick(stop, Qt.MouseButton.LeftButton)
                assert online.keys() == devices
                assert all(sequences and sequences[0] == 0 for sequences in samples.values())
                if not stop_manually:
                    assert all(sequences == [0, 1] for sequences in samples.values())
                while not start.isEnabled():
                    await asyncio.sleep(0.01)
                assert not stop.isEnabled()
                assert status.text() == ("Parado" if stop_manually else "Concluído")
                pump.cancel()


@pytest.mark.parametrize("stop_manually", [False, True], ids=["natural", "botao-parar"])
def test_gui_publishes_fleet_and_announces_graceful_shutdown(
    fleet_window: tuple[QApplication, FleetWindow],
    mosquitto_executable: str,
    unused_tcp_port: int,
    mosquitto_config: Path,
    stop_manually: bool,
) -> None:
    """Confirma telemetria e offline normal ao concluir ou clicar em Parar."""
    app, window = fleet_window
    asyncio.run(
        _exercise_fleet(
            mosquitto_executable, unused_tcp_port, mosquitto_config, app, window, stop_manually
        ),
        loop_factory=asyncio.SelectorEventLoop,
    )
