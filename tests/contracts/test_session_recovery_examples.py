"""Verifica a consistência dos exemplos documentados de recuperação MQTT."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest


@pytest.fixture
def recovery_example() -> dict[str, dict[str, Any]]:
    """Carrega as mensagens do cenário de referência do contrato."""
    fixture_path = Path(__file__).parent / "fixtures" / "session_recovery.json"
    example = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert isinstance(example, dict)
    return example


def test_recovery_messages_keep_device_identity(
    recovery_example: dict[str, dict[str, Any]],
) -> None:
    """Verifica a identidade estável do dispositivo e o envelope dos exemplos MQTT."""
    scenario_metadata = {"offline_transition", "late_delivery"}
    messages = [
        payload for name, payload in recovery_example.items() if name not in scenario_metadata
    ]
    assert {payload["device_id"] for payload in messages} == {"esp32-001"}
    assert all(
        type(payload["schema_version"]) is int and payload["schema_version"] == 1
        for payload in messages
    )
    assert len({UUID(payload["message_id"]) for payload in messages}) == len(messages)


def test_recovery_commands_target_current_boot(recovery_example: dict[str, dict[str, Any]]) -> None:
    """Verifica o comando rejeitado após a queda e a confirmação da sessão nova."""
    original = recovery_example["original_command"]
    online = recovery_example["reconnected_status"]
    rejected = recovery_example["rejected_ack"]
    current = recovery_example["current_command"]
    accepted = recovery_example["current_ack"]
    offline_transition = recovery_example["offline_transition"]

    assert original["boot_id"] == recovery_example["original_status"]["boot_id"]
    assert UUID(original["boot_id"]) != UUID(online["boot_id"])
    assert online["state"] == "online" and online["reason"] == "connected"
    assert rejected["boot_id"] == current["boot_id"] == online["boot_id"]
    assert rejected["command_id"] == original["command_id"]
    assert rejected["accepted"] is False
    assert rejected["reason"] == "INVALID_BOOT"
    assert rejected["state"] == "IDLE" and rejected["session_id"] is None
    assert rejected["session_version"] == 0
    assert (
        datetime.fromisoformat(online["occurred_at"])
        < datetime.fromisoformat(rejected["occurred_at"])
        < datetime.fromisoformat(original["expires_at"])
    )

    assert current["action"] == original["action"] == "START"
    assert current["command_id"] != original["command_id"]
    assert current["session_id"] != original["session_id"]
    assert offline_transition["previous_session_id"] == original["session_id"]
    assert offline_transition["current_session_id"] == current["session_id"]
    assert (
        original["session_version"]
        < offline_transition["session_version"]
        < current["session_version"]
    )
    assert (
        datetime.fromisoformat(online["occurred_at"])
        < datetime.fromisoformat(current["occurred_at"])
        < datetime.fromisoformat(accepted["occurred_at"])
        < datetime.fromisoformat(current["expires_at"])
    )
    assert accepted["accepted"] is True
    assert accepted["reason"] == "APPLIED" and accepted["state"] == "ACTIVE"
    for field in ("command_id", "boot_id", "session_id", "session_version"):
        assert accepted[field] == current[field]


def test_delayed_reading_keeps_original_session(
    recovery_example: dict[str, dict[str, Any]],
) -> None:
    """Verifica que a leitura associada à sessão A chega após a ativação de B."""
    delayed = recovery_example["delayed_reading"]
    original = recovery_example["original_command"]
    current = recovery_example["current_reading"]
    accepted = recovery_example["current_ack"]
    offline_transition = recovery_example["offline_transition"]
    late_delivery = recovery_example["late_delivery"]

    assert late_delivery["message_id"] == delayed["message_id"]
    for field in ("boot_id", "session_id", "session_version"):
        assert delayed[field] == original[field]
        assert current[field] == accepted[field]
        assert delayed[field] != current[field]
    assert current["sequence_number"] == 0
    assert (
        datetime.fromisoformat(delayed["sampled_at"])
        < datetime.fromisoformat(offline_transition["disconnected_at"])
        < datetime.fromisoformat(offline_transition["changed_at"])
        < datetime.fromisoformat(recovery_example["reconnected_status"]["occurred_at"])
        < datetime.fromisoformat(accepted["occurred_at"])
        < datetime.fromisoformat(current["sampled_at"])
        < datetime.fromisoformat(late_delivery["received_at"])
    )
