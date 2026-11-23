"""Executa a CLI em outro processo e observa a frota pelo broker."""

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
from esp32_fleet.domain.vitals import VitalsGenerator, VitalsScenario

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


async def _verify_fleet(executable: str, port: int, config: Path) -> None:
    device_ids = {"esp32-001", "esp32-002", "esp32-003"}
    target = "esp32-001"
    session_id = str(uuid4())
    online: dict[str, str] = {}
    offline: dict[str, str] = {}
    samples: dict[str, list[dict[str, Any]]] = {device_id: [] for device_id in device_ids}
    acks: list[dict[str, Any]] = []
    command_id = None

    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as controller:
            await controller.subscribe("vitals/+/+", qos=1)
            args = [
                sys.executable,
                "-m",
                "esp32_fleet",
                "--port",
                str(port),
                "--devices",
                "3",
                "--interval",
                "0.8",
                "--samples",
                "4",
                "--seed",
                "42",
                "--scenario",
                "temperature-rise",
            ]
            with _running_process(args, config.with_name("fleet.log")) as cli:
                async with asyncio.timeout(15):
                    while offline.keys() != device_ids or sum(map(len, samples.values())) < 12:
                        topic, payload = await _message(controller)
                        _, device_id, channel = topic.split("/")
                        assert device_id in device_ids and payload["device_id"] == device_id
                        if channel == "status" and payload["state"] == "online":
                            online[device_id] = payload["boot_id"]
                        elif channel == "status":
                            assert payload["reason"] == "graceful_shutdown"
                            offline[device_id] = payload["boot_id"]
                        elif channel == "data":
                            samples[device_id].append(payload)
                        elif channel == "ack":
                            acks.append(payload)

                        if command_id is None and target in online and samples[target]:
                            command_id, start = _command(
                                SessionAction.START, online[target], session_id, 1
                            )
                            await controller.publish(f"vitals/{target}/command", start, qos=1)

                assert cli.wait(timeout=5) == 0
                assert online.keys() == device_ids and online == offline
                assert len(set(online.values())) == len(device_ids)
                assert len(acks) == 1
                ack = acks[0]
                assert ack["device_id"] == target and ack["command_id"] == command_id
                assert ack["accepted"] is True and ack["boot_id"] == online[target]
                assert (
                    len(
                        {
                            UUID(sample["message_id"])
                            for batch in samples.values()
                            for sample in batch
                        }
                    )
                    == 12
                )
                for seed, device_id in enumerate(sorted(samples), start=42):
                    batch = samples[device_id]
                    generator = VitalsGenerator(seed=seed, scenario=VitalsScenario.TEMPERATURE_RISE)
                    assert [sample["temperature_c"] for sample in batch] == [
                        generator.generate().temperature_c for _ in batch
                    ]
                    assert [sample["sequence_number"] for sample in batch] == [0, 1, 2, 3]
                    assert {sample["boot_id"] for sample in batch} == {online[device_id]}
                    assert batch[0]["session_id"] is None
                    if device_id == target:
                        assert batch[-1]["session_id"] == session_id
                        assert batch[-1]["session_version"] == 1
                    else:
                        assert all(
                            sample["session_id"] is None and sample["session_version"] == 0
                            for sample in batch
                        )


def test_cli_runs_a_fleet_with_independent_sessions_and_samples(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Confirma o cenário na frota, o encerramento e o isolamento da sessão."""
    asyncio.run(
        _verify_fleet(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )
