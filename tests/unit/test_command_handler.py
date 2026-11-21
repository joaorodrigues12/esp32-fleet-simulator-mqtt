"""Verifica validação, correlação e saída dos comandos MQTT de sessão."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import aiomqtt
import pytest

from esp32_fleet.handlers.commands import CommandHandler, CommandReplies
from esp32_fleet.mqtt.connection import MqttConnection

_NOW = datetime(2026, 11, 20, 18, 0, 3, tzinfo=UTC)


@pytest.fixture
def original_command() -> dict[str, object]:
    """Usa o comando JSON já documentado no cenário de recuperação."""
    fixture = Path(__file__).parents[1] / "contracts" / "fixtures" / "session_recovery.json"
    scenario = json.loads(fixture.read_text(encoding="utf-8"))
    command = scenario["original_command"]
    assert isinstance(command, dict)
    return command


def test_valid_start_generates_correlated_ack_and_event(
    original_command: dict[str, object],
) -> None:
    """Verifica os envelopes de saída e a associação observada pelo dispositivo."""
    handler = CommandHandler("esp32-001")
    boot_id = UUID(str(original_command["boot_id"]))
    encoded = json.dumps(original_command).encode("utf-8")
    replies = handler.handle(encoded, boot_id, now=_NOW)

    assert replies is not None and replies.event is not None
    ack, event = json.loads(replies.ack), json.loads(replies.event)
    for message in (ack, event):
        assert message["schema_version"] == 1
        assert message["occurred_at"] == _NOW.isoformat()
        assert message["state"] == "ACTIVE"
        for field in ("device_id", "boot_id", "session_id", "session_version"):
            assert message[field] == original_command[field]
    assert ack["accepted"] is True and ack["reason"] == "APPLIED"
    assert ack["command_id"] == event["cause_command_id"] == original_command["command_id"]
    assert UUID(ack["message_id"]) != UUID(event["message_id"])

    duplicate = handler.handle(encoded, boot_id, now=_NOW)
    assert duplicate is not None and duplicate.event is None
    duplicate_ack = json.loads(duplicate.ack)
    assert duplicate_ack["accepted"] is True and duplicate_ack["reason"] == "DUPLICATE"
    assert duplicate_ack["command_id"] == ack["command_id"]
    assert duplicate_ack["message_id"] != ack["message_id"]


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"occurred_at": 1_795_197_600},
        {"occurred_at": "2026-11-20T18:00:00"},
        {"occurred_at": "2026-11-20T18:00:00+01:00"},
        {"expires_at": "2026-11-20T18:00:00Z"},
        {"session_version": "3"},
        {"session_version": 3.0},
        {"session_version": -1},
        {"session_id": None},
        {"action": "PAUSE"},
        {"unknown_field": 10},
        {"device_id": "esp32-002"},
    ],
)
def test_invalid_correlatable_command_generates_only_ack(
    original_command: dict[str, object], changes: dict[str, object]
) -> None:
    """Rejeita campos inválidos, desconhecidos ou incompatíveis com o tópico."""
    handler = CommandHandler("esp32-001")
    boot_id = UUID(str(original_command["boot_id"]))
    invalid = json.dumps({**original_command, **changes}).encode("utf-8")

    replies = handler.handle(invalid, boot_id, now=_NOW)

    assert replies is not None and replies.event is None
    ack = json.loads(replies.ack)
    assert ack["reason"] == "INVALID_PAYLOAD" and ack["accepted"] is False
    assert ack["command_id"] == original_command["command_id"]
    assert ack["boot_id"] == str(boot_id) and ack["device_id"] == "esp32-001"
    assert ack["state"] == "IDLE" and ack["session_id"] is None and ack["session_version"] == 0


@pytest.mark.parametrize(
    "payload",
    [b"", b"{", b"[]", b"\xff", b'{"command_id":"id-invalido"}', b" " * 16_385],
)
def test_payload_without_valid_correlation_has_no_reply(
    payload: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """Registra mensagens que não admitem ACK correlacionado."""
    assert CommandHandler("esp32-001").handle(payload, uuid4(), now=_NOW) is None
    assert "esp32-001" in caplog.text


def test_new_boot_resets_the_handler_and_rejects_the_old_command(
    original_command: dict[str, object],
) -> None:
    """Responde com o boot receptor sem restaurar o vínculo do anterior."""
    handler = CommandHandler("esp32-001")
    old_boot = UUID(str(original_command["boot_id"]))
    new_boot = uuid4()
    payload = json.dumps(original_command).encode("utf-8")
    applied = handler.handle(payload, old_boot, now=_NOW)
    assert applied is not None and applied.event is not None

    rejected = handler.handle(payload, new_boot, now=_NOW)

    assert rejected is not None and rejected.event is None
    ack = json.loads(rejected.ack)
    assert ack["reason"] == "INVALID_BOOT" and ack["accepted"] is False
    assert ack["boot_id"] == str(new_boot)
    assert ack["command_id"] == original_command["command_id"]
    assert ack["state"] == "IDLE" and ack["session_version"] == 0


@pytest.mark.asyncio
async def test_lost_ack_allows_the_controller_to_retry_on_the_same_boot(
    original_command: dict[str, object],
) -> None:
    """O evento ainda é enviado e uma repetição recupera o ACK sem novo evento."""
    handler = CommandHandler("esp32-001")
    incoming: asyncio.Queue[aiomqtt.Message] = asyncio.Queue()
    published: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
    ack_failed = False

    async def messages() -> AsyncIterator[aiomqtt.Message]:
        while True:
            yield await incoming.get()

    async def send(topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        nonlocal ack_failed
        if topic.endswith("/status"):
            return
        assert qos == 1 and retain is False
        if topic.endswith("/ack") and not ack_failed:
            ack_failed = True
            raise aiomqtt.MqttError("ACK sem confirmação de publicação")
        published.put_nowait((topic, payload))

    def handle(payload: bytes, boot_id: UUID) -> CommandReplies | None:
        return handler.handle(payload, boot_id, now=_NOW)

    client = AsyncMock(spec=aiomqtt.Client)
    client.__aenter__.return_value = client
    client.subscribe.return_value = (1,)
    client.messages = messages()
    client.publish.side_effect = send

    with patch("esp32_fleet.mqtt.connection.aiomqtt.Client", return_value=client):
        async with MqttConnection("esp32-001", on_session_command=handle) as connection:
            command = {**original_command, "boot_id": str(connection.boot_id)}
            message = Mock(payload=json.dumps(command).encode("utf-8"), retain=False)
            incoming.put_nowait(message)
            event_topic, event = await asyncio.wait_for(published.get(), timeout=2)
            assert event_topic == "vitals/esp32-001/session"
            assert json.loads(event)["cause_command_id"] == command["command_id"]

            incoming.put_nowait(message)
            ack_topic, ack = await asyncio.wait_for(published.get(), timeout=2)
            assert ack_topic == "vitals/esp32-001/ack"
            assert json.loads(ack)["reason"] == "DUPLICATE"
            assert published.empty()
