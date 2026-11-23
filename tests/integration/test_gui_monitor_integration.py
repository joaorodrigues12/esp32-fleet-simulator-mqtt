"""Verifica o monitor MQTT da janela com frota real e reinício do broker."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import aiomqtt
import pytest
from test_gui_fleet import fleet_window as fleet_window
from test_mqtt_connection_integration import _broker, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable

from esp32_fleet.mqtt.presence import DevicePresence

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication, QTableWidget

    from esp32_fleet.gui.window import FleetWindow


async def _wait_for_gui(app: QApplication, condition: Callable[[], bool], failure: str) -> None:
    try:
        async with asyncio.timeout(15):
            while not condition():
                app.processEvents()
                await asyncio.sleep(0.01)
    except TimeoutError:
        pytest.fail(failure)


def _states(table: QTableWidget) -> dict[str, str]:
    states: dict[str, str] = {}
    for row in range(table.rowCount()):
        device = table.item(row, 1)
        state = table.item(row, 2)
        assert device is not None and state is not None
        states[device.text()] = state.text()
    return states


async def _exercise_monitor(
    executable: str, port: int, config: Path, app: QApplication, window: FleetWindow
) -> None:
    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QTableWidget

    for name, value in {
        "deviceId": "monitorada",
        "devices": "50",
        "interval": "0.25",
        "port": str(port),
    }.items():
        field = window.findChild(QLineEdit, name)
        assert field is not None
        field.setText(value)
    table = window.findChild(QTableWidget, "devicePreview")
    label = window.findChild(QLabel, "mqttMonitorStatus")
    start = window.findChild(QPushButton, "startButton")
    stop = window.findChild(QPushButton, "stopButton")
    assert table is not None and label is not None and start is not None and stop is not None
    expected_ids = {f"monitorada-{number:03d}" for number in range(1, 51)}
    assert _states(table) == dict.fromkeys(expected_ids, "Aguardando")

    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        start.click()
        await _wait_for_gui(
            app,
            lambda: _states(table) == dict.fromkeys(expected_ids, "Online"),
            "O monitor não recebeu online para os 50 dispositivos.",
        )
        assert label.text() == "Monitor MQTT conectado"

        async with aiomqtt.Client("127.0.0.1", port, timeout=3) as publisher:
            first = DevicePresence("monitorada-001", uuid4(), 10)
            await publisher.publish(first.topic, b'{"state":"offline"}', qos=1)
            # O segundo anúncio sinaliza que o monitor já recebeu a mensagem anterior.
            last = DevicePresence("monitorada-050", uuid4(), 10)
            await last.publish(publisher, "unexpected_disconnect")
            await _wait_for_gui(
                app,
                lambda: _states(table)[last.device_id] == "Offline",
                "O monitor não mostrou o anúncio offline.",
            )
            assert _states(table)[first.device_id] == "Online"

        assert stop.isEnabled()
        stop.click()
        await _wait_for_gui(
            app,
            lambda: start.isEnabled() and _states(table) == dict.fromkeys(expected_ids, "Offline"),
            "A janela não mostrou o offline de toda a frota após a parada.",
        )

    await _wait_for_gui(
        app,
        lambda: "desconectado" in label.text(),
        "A janela não detectou a queda do broker.",
    )
    assert _states(table) == dict.fromkeys(expected_ids, "Aguardando")

    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=3) as publisher:
            await first.publish(publisher, "connected")
            await _wait_for_gui(
                app,
                lambda: _states(table)[first.device_id] == "Online",
                "O monitor não se reconectou nem leu o status retido.",
            )
            assert label.text() == "Monitor MQTT conectado"
            assert _states(table) == dict.fromkeys(expected_ids, "Aguardando") | {
                first.device_id: "Online"
            }

        field = window.findChild(QLineEdit, "deviceId")
        assert field is not None
        field.setText("outra-frota")
        await _wait_for_gui(app, start.isEnabled, "O monitor da configuração anterior não fechou.")
        assert label.text() == "Monitor MQTT inativo"
        assert _states(table) == dict.fromkeys(
            (f"outra-frota-{number:03d}" for number in range(1, 51)), "Aguardando"
        )


def test_gui_monitors_50_devices_and_recovers_after_broker_restart(
    fleet_window: tuple[QApplication, FleetWindow],
    mosquitto_executable: str,
    unused_tcp_port: int,
    mosquitto_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirma estados por dispositivo, desconexão, nova assinatura e reset da tabela."""
    for name in ("USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"ESP32_FLEET_MQTT_MONITOR_{name}", raising=False)
    app, window = fleet_window
    asyncio.run(
        _exercise_monitor(mosquitto_executable, unused_tcp_port, mosquitto_config, app, window),
        loop_factory=asyncio.SelectorEventLoop,
    )
