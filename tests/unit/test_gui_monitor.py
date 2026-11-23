"""Valida a identidade e o envelope dos anúncios exibidos pelo monitor."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from esp32_fleet.config.settings import parse_settings

pytest.importorskip("PySide6.QtCore")

from esp32_fleet.gui.monitor import MonitorCredentials, status_from_message


def _payload(**overrides: object) -> bytes:
    values = {
        "schema_version": 1,
        "device_id": "frota-001",
        "message_id": str(uuid4()),
        "boot_id": str(uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "state": "online",
        "reason": "connected",
        "heartbeat_interval_seconds": 10,
        **overrides,
    }
    return json.dumps(values).encode()


def test_status_accepts_only_ids_generated_for_the_fleet() -> None:
    """Confere os limites e a numeração canônica da configuração."""
    settings = parse_settings(["--device-id=frota", "--devices=50"])
    assert status_from_message(settings, "vitals/frota-001/status", _payload()) == (
        "frota-001",
        "online",
    )
    final_payload = _payload(device_id="frota-050", state="offline", reason="unexpected_disconnect")
    assert status_from_message(settings, "vitals/frota-050/status", final_payload) == (
        "frota-050",
        "offline",
    )
    for device_id in ("frota", "frota-000", "frota-051", "frota-1", "frota-٠٠١", "vizinha-001"):
        assert (
            status_from_message(
                settings, f"vitals/{device_id}/status", _payload(device_id=device_id)
            )
            is None
        )


def test_status_requires_topic_and_payload_to_identify_the_same_device() -> None:
    """Valida o tópico exato também em um ensaio de dispositivo único."""
    settings = parse_settings(["--device-id=frota-001"])
    assert status_from_message(settings, "vitals/frota-001/status", _payload()) == (
        "frota-001",
        "online",
    )
    assert (
        status_from_message(settings, "vitals/frota-001/status", _payload(device_id="frota-002"))
        is None
    )
    assert status_from_message(settings, "vitals/frota-001/data", _payload()) is None
    assert status_from_message(settings, "vitals/frota-001/status/extra", _payload()) is None


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"x" * (16 * 1024 + 1),
        "payload de tipo inesperado",
        _payload(schema_version=True),
        _payload(schema_version=2),
        _payload(boot_id="inválido"),
        _payload(occurred_at="2026-11-23T12:12:12"),
        _payload(occurred_at="2026-11-23T12:12:12-03:00"),
        _payload(heartbeat_interval_seconds=0),
        _payload(state="offline"),
    ],
)
def test_status_ignores_invalid_payloads(payload: object) -> None:
    """Rejeita mensagens que não cumprem o contrato sem afetar a escuta."""
    settings = parse_settings(["--device-id=frota-001"])
    assert status_from_message(settings, "vitals/frota-001/status", payload) is None


def test_monitor_credentials_can_use_a_separate_read_only_account() -> None:
    """Mantém as credenciais do simulador como padrão e valida o par opcional."""
    settings = replace(parse_settings([]), username="simulador", password="senha-do-simulador")
    assert MonitorCredentials.resolve(settings, None, None) == MonitorCredentials(
        "simulador", "senha-do-simulador"
    )
    assert MonitorCredentials.resolve(settings, "leitor", "senha-de-leitura") == MonitorCredentials(
        "leitor", "senha-de-leitura"
    )
    with pytest.raises(ValueError, match="em conjunto"):
        MonitorCredentials.resolve(settings, "leitor", None)
