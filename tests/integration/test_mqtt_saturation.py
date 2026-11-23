"""Verifica o comportamento de uma fila de comandos cheia com Mosquitto."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import aiomqtt
import pytest
from test_mqtt_connection_integration import _broker, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable
from test_mqtt_sessions import _ACK, _COMMAND, _DEVICE_ID, _STATUS, _command, _receive

from esp32_fleet.domain.sessions import SessionAction
from esp32_fleet.handlers.commands import CommandHandler
from esp32_fleet.mqtt.connection import MqttConnection

_QUEUE_WARNING = "Message queue is full. Discarding message."


async def _verify_saturation(
    executable: str, port: int, config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first_ack_started = asyncio.Event()
    release_first_ack = asyncio.Event()
    real_publish = aiomqtt.Client.publish

    async def pause_first_ack(
        client: aiomqtt.Client, topic: str, payload: bytes, *, qos: int, retain: bool = False
    ) -> None:
        # Pausa só a resposta da aplicação. O cliente continua recebendo do broker.
        if topic == _ACK and not first_ack_started.is_set():
            first_ack_started.set()
            await release_first_ack.wait()
        await real_publish(client, topic, payload, qos=qos, retain=retain)

    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as controller:
            await controller.subscribe(_STATUS, qos=1)
            await controller.subscribe(_ACK, qos=1)
            handler = CommandHandler(_DEVICE_ID)
            connection = MqttConnection(
                _DEVICE_ID, port=port, on_session_command=handler.handle, command_queue_size=2
            )
            with patch.object(aiomqtt.Client, "publish", new=pause_first_ack):
                async with connection, asyncio.timeout(8):
                    online = (await _receive(controller, {_STATUS}))[_STATUS]
                    boot = online["boot_id"]
                    session = str(uuid4())
                    commands = [
                        _command(SessionAction.START, boot, session, revision)
                        for revision in range(1, 7)
                    ]

                    await controller.publish(_COMMAND, commands[0][1], qos=1)
                    await first_ack_started.wait()
                    for _, payload in commands[1:5]:
                        await controller.publish(_COMMAND, payload, qos=1)

                    while caplog.messages.count(_QUEUE_WARNING) < 2:
                        await asyncio.sleep(0.01)
                    release_first_ack.set()

                    for revision, (command_id, _) in enumerate(commands[:3], start=1):
                        ack = (await _receive(controller, {_ACK}))[_ACK]
                        assert ack["accepted"] is True
                        assert (ack["command_id"], ack["session_version"]) == (command_id, revision)

                    # O próximo ACK confirma a retomada e a ausência dos comandos descartados.
                    next_id, next_payload = commands[5]
                    await controller.publish(_COMMAND, next_payload, qos=1)
                    ack = (await _receive(controller, {_ACK}))[_ACK]
                    assert ack["accepted"] is True
                    assert (ack["command_id"], ack["session_version"]) == (next_id, 6)

    assert caplog.messages.count(_QUEUE_WARNING) == 2


def test_command_queue_discards_excess_and_resumes_in_order(
    mosquitto_executable: str,
    unused_tcp_port: int,
    mosquitto_config: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Confirma FIFO, aviso por descarte e retomada quando a fila esvazia."""
    with caplog.at_level(logging.WARNING, logger="mqtt"):
        asyncio.run(
            _verify_saturation(mosquitto_executable, unused_tcp_port, mosquitto_config, caplog),
            loop_factory=asyncio.SelectorEventLoop,
        )
