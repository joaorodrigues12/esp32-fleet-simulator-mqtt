"""Testa comandos, ACKs e eventos de sessão com o Mosquitto."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiomqtt
import pytest
from test_mqtt_connection_integration import _broker, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable

from esp32_fleet.domain.sessions import SessionAction
from esp32_fleet.handlers.commands import CommandHandler
from esp32_fleet.mqtt.connection import MqttConnection

_DEVICE_ID = "esp32-001"
_STATUS = f"vitals/{_DEVICE_ID}/status"
_COMMAND = f"vitals/{_DEVICE_ID}/command"
_ACK = f"vitals/{_DEVICE_ID}/ack"
_EVENT = f"vitals/{_DEVICE_ID}/session"


def _command(
    action: SessionAction, boot_id: str, session_id: str, revision: int
) -> tuple[str, bytes]:
    now = datetime.now(UTC)
    command_id = str(uuid4())
    payload = {
        "schema_version": 1,
        "message_id": str(uuid4()),
        "command_id": command_id,
        "device_id": _DEVICE_ID,
        "boot_id": boot_id,
        "occurred_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=30)).isoformat(),
        "action": action,
        "session_id": session_id,
        "session_version": revision,
    }
    return command_id, json.dumps(payload).encode("utf-8")


async def _receive(client: aiomqtt.Client, topics: set[str]) -> dict[str, dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    async with asyncio.timeout(6):
        while len(messages) < len(topics):
            message = await anext(client.messages)
            topic = str(message.topic)
            assert topic in topics and topic not in messages
            assert message.qos == 1 and not message.retain
            assert isinstance(message.payload, bytes)
            payload = json.loads(message.payload)
            assert isinstance(payload, dict)
            messages[topic] = payload
    return messages


def _assert_applied(
    replies: dict[str, dict[str, Any]],
    command_id: str,
    boot_id: str,
    revision: int,
    session_id: str | None,
) -> None:
    ack, event = replies[_ACK], replies[_EVENT]
    assert ack["accepted"] is True and ack["reason"] == "APPLIED"
    assert ack["command_id"] == event["cause_command_id"] == command_id
    assert UUID(ack["message_id"]) != UUID(event["message_id"])
    for payload in (ack, event):
        assert payload["boot_id"] == boot_id and payload["device_id"] == _DEVICE_ID
        assert payload["session_version"] == revision and payload["session_id"] == session_id
        assert payload["state"] == ("IDLE" if session_id is None else "ACTIVE")


async def _verify_sessions(executable: str, port: int, config: Path) -> None:
    with _broker(executable, config) as process:
        await _wait_for_broker(process, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as controller:
            for topic in (_STATUS, _ACK, _EVENT):
                await controller.subscribe(topic, qos=1)
            handler = CommandHandler(_DEVICE_ID)
            async with MqttConnection(_DEVICE_ID, port=port, on_session_command=handler.handle):
                online = (await _receive(controller, {_STATUS}))[_STATUS]
                boot_id = str(online["boot_id"])
                session_id = str(uuid4())
                start_id, start = _command(SessionAction.START, boot_id, session_id, 1)
                await controller.publish(_COMMAND, start, qos=1)
                replies = await _receive(controller, {_ACK, _EVENT})
                _assert_applied(replies, start_id, boot_id, 1, session_id)

                await controller.publish(_COMMAND, start, qos=1)
                duplicate = (await _receive(controller, {_ACK}))[_ACK]
                assert duplicate["reason"] == "DUPLICATE" and duplicate["accepted"] is True
                assert duplicate["command_id"] == start_id and duplicate["session_version"] == 1

                end_id, end = _command(SessionAction.END, boot_id, session_id, 2)
                await controller.publish(_COMMAND, end, qos=1)
                replies = await _receive(controller, {_ACK, _EVENT})
                _assert_applied(replies, end_id, boot_id, 2, None)

                async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as newcomer:
                    for topic in (_ACK, _EVENT):
                        await newcomer.subscribe(topic, qos=1)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(anext(newcomer.messages), timeout=0.25)


def test_session_commands_produce_correlated_responses_in_mosquitto(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Verifica START, duplicata e END de ponta a ponta, sem respostas retidas."""
    asyncio.run(
        _verify_sessions(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )
