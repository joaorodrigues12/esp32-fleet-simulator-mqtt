"""Executa a CLI em outro processo e observa o dispositivo pelo broker."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiomqtt
from test_mqtt_connection_integration import _broker, _running_process, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable
from test_mqtt_sessions import _command

from esp32_fleet.domain.sessions import SessionAction

_TOPIC = "vitals/esp32-001"


async def _message(client: aiomqtt.Client) -> tuple[str, dict[str, Any]]:
    message = await anext(client.messages)
    assert message.qos == 1 and isinstance(message.payload, bytes)
    payload = json.loads(message.payload)
    assert isinstance(payload, dict)
    return str(message.topic), payload


async def _verify_cli(executable: str, port: int, config: Path) -> None:
    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as controller:
            await controller.subscribe(f"{_TOPIC}/+", qos=1)
            args = [
                sys.executable,
                "-m",
                "esp32_fleet",
                "--port",
                str(port),
                "--interval",
                "0.8",
                "--seed",
                "42",
                "--samples",
                "3",
            ]
            with _running_process(args, config.with_name("cli.log")) as cli:
                samples: list[dict[str, Any]] = []
                online: dict[str, Any] | None = None
                async with asyncio.timeout(12):
                    while online is None or not samples:
                        topic, payload = await _message(controller)
                        if topic == f"{_TOPIC}/status":
                            assert payload["state"] == "online"
                            online = payload
                        elif topic == f"{_TOPIC}/data":
                            samples.append(payload)
                    boot_id = online["boot_id"]
                    session_id = str(uuid4())
                    command_id, start = _command(SessionAction.START, boot_id, session_id, 1)
                    await controller.publish(f"{_TOPIC}/command", start, qos=1)
                    ack = None
                    while True:
                        topic, payload = await _message(controller)
                        if topic == f"{_TOPIC}/ack":
                            ack = payload
                        elif topic == f"{_TOPIC}/data":
                            samples.append(payload)
                        elif topic == f"{_TOPIC}/status" and payload["state"] == "offline":
                            assert payload["reason"] == "graceful_shutdown"
                            assert payload["boot_id"] == boot_id
                            break

                assert cli.wait(timeout=5) == 0
                assert ack is not None and ack["accepted"] is True
                assert ack["command_id"] == command_id and ack["reason"] == "APPLIED"
                assert [sample["sequence_number"] for sample in samples] == [0, 1, 2]
                assert {sample["boot_id"] for sample in samples} == {boot_id}
                assert len({UUID(sample["message_id"]) for sample in samples}) == 3
                assert samples[0]["session_id"] is None and samples[0]["session_version"] == 0
                assert (
                    samples[-1]["session_id"] == session_id and samples[-1]["session_version"] == 1
                )
                assert samples[0]["heart_rate_bpm"] == 82
                assert samples[0]["temperature_c"] == 36.67


def test_cli_publishes_samples_with_the_current_session_and_shuts_down(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Confirma telemetria, vínculo e offline ao atingir o limite de amostras."""
    asyncio.run(
        _verify_cli(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )
