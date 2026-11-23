"""Verifica a retomada de uma frota MQTT após a interrupção do broker local."""

import asyncio
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID

import aiomqtt
import pytest
from test_mqtt_cli import _message
from test_mqtt_connection_integration import _broker, _running_process, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable

_DEVICE_IDS = {"esp32-001", "esp32-002", "esp32-003"}


async def _observe_fleet(
    observer: aiomqtt.Client, previous_boots: dict[str, UUID]
) -> dict[str, UUID]:
    """Espera presença e telemetria do mesmo boot para cada dispositivo."""
    online: dict[str, UUID] = {}
    readings: dict[str, UUID] = {}
    try:
        async with asyncio.timeout(12):
            while online.keys() != _DEVICE_IDS or readings != online:
                topic, payload = await _message(observer)
                _, device_id, channel = topic.split("/")
                assert device_id in _DEVICE_IDS and payload["device_id"] == device_id
                boot = UUID(payload["boot_id"])
                if channel == "status" and payload["state"] == "online":
                    assert payload["reason"] == "connected"
                    assert boot != previous_boots.get(device_id)
                    online[device_id] = boot
                elif channel == "data" and boot != previous_boots.get(device_id):
                    # Uma leitura reenviada do boot anterior não comprova nova amostragem.
                    readings[device_id] = boot
    except TimeoutError:
        pytest.fail(f"Frota incompleta. Presença por boot {online}. Leituras por boot {readings}.")
    return online


async def _verify_fleet_recovery(executable: str, port: int, config: Path) -> None:
    args = [
        sys.executable,
        "-m",
        "esp32_fleet",
        "--port",
        str(port),
        "--devices",
        str(len(_DEVICE_IDS)),
        "--interval",
        "0.25",
        "--samples",
        "12",
        "--seed",
        "42",
        "--omit-every",
        "4",
        "--metrics",
    ]
    log = config.with_name("fleet-recovery.log")
    with ExitStack() as fleet_scope:
        with _broker(executable, config) as broker:
            await _wait_for_broker(broker, port, config)
            async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as observer:
                await observer.subscribe("vitals/+/+", qos=1)
                fleet = fleet_scope.enter_context(_running_process(args, log))
                original_boots = await _observe_fleet(observer, {})

            assert fleet.poll() is None
            broker.kill()
            await asyncio.to_thread(broker.wait, timeout=5)

        # Mantém o broker fora do ar por mais de um intervalo de coleta da frota.
        await asyncio.sleep(0.4)
        assert fleet.poll() is None, log.read_text(encoding="utf-8", errors="replace")

        with _broker(executable, config) as broker:
            await _wait_for_broker(broker, port, config)
            async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as observer:
                await observer.subscribe("vitals/+/+", qos=1)
                await _observe_fleet(observer, original_boots)

            exit_code = await asyncio.to_thread(fleet.wait, timeout=8)
            output = log.read_text(encoding="utf-8", errors="replace")
            assert exit_code == 0, output
            summaries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
            assert len(summaries) == len(_DEVICE_IDS), output
            assert {summary["device_id"] for summary in summaries} == _DEVICE_IDS
            for summary in summaries:
                assert summary["event"] == "telemetry_metrics"
                assert summary["samples_acked"] == 9 and summary["samples_omitted"] == 3
                assert summary["reconnections"] == 1
                assert summary["mqtt_errors"] >= 1


def test_fleet_resumes_publishing_after_mosquitto_restart(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Confirma a retomada dos três dispositivos e seus contadores individuais."""
    asyncio.run(
        _verify_fleet_recovery(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )
