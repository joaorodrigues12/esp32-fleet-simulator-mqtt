"""Verifica a política de reconexão e a publicação MQTT sem um broker."""

from unittest.mock import AsyncMock, patch

import aiomqtt
import pytest

from esp32_fleet.mqtt.connection import MqttConnection
from esp32_fleet.mqtt.reconnect import ConnectionPolicy


def test_backoff_grows_and_caps_the_jitter() -> None:
    """Verifica que o jitter acompanha o crescimento e respeita o teto."""
    policy = ConnectionPolicy(max_retries=5, initial_delay=1.0, max_delay=3.0)
    delays = list(policy.retry_delays())

    for delay, limit in zip(delays, (1.0, 2.0, 3.0, 3.0, 3.0), strict=True):
        assert limit / 2 <= delay <= limit


@pytest.mark.parametrize(
    ("retries", "initial", "maximum"),
    [(-1, 0.25, 4.0), (1, 0.0, 4.0), (1, 2.0, 1.0), (1, 1.0, float("inf"))],
)
def test_policy_rejects_invalid_limits(retries: int, initial: float, maximum: float) -> None:
    """Rejeita configurações que produziriam tentativas ou esperas inválidas."""
    with pytest.raises(ValueError):
        ConnectionPolicy(max_retries=retries, initial_delay=initial, max_delay=maximum)


@pytest.mark.asyncio
async def test_publish_retries_same_payload_on_a_new_client() -> None:
    """Verifica reconexão, preservação do payload e reutilização do cliente recuperado."""
    original = AsyncMock(spec=aiomqtt.Client)
    original.__aenter__.return_value = original
    original.publish.side_effect = aiomqtt.MqttError("conexão perdida")
    recovered = AsyncMock(spec=aiomqtt.Client)
    recovered.__aenter__.return_value = recovered
    policy = ConnectionPolicy(max_retries=1, initial_delay=0.001, max_delay=0.001)
    payload = b'{"message_id":"amostra-repetida"}'
    topic = "vitals/esp32-001/data"

    with patch(
        "esp32_fleet.mqtt.connection.aiomqtt.Client", side_effect=[original, recovered]
    ) as factory:
        async with MqttConnection("esp32-001", policy=policy) as connection:
            await connection.publish_telemetry(payload)
            await connection.publish_telemetry(b"{}")
            assert factory.call_count == 2

    original.publish.assert_awaited_once_with(topic, payload, qos=1, retain=False)
    assert recovered.publish.await_args_list[0].args == (topic, payload)
    assert recovered.publish.await_args_list[0].kwargs == {"qos": 1, "retain": False}
    recovered.publish.assert_awaited_with(topic, b"{}", qos=1, retain=False)
    original.__aexit__.assert_awaited_once()
    recovered.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_error_is_reported_after_retry_budget() -> None:
    """Propaga a falha depois de consumir o limite de reconexões."""
    unavailable = AsyncMock(spec=aiomqtt.Client)
    unavailable.__aenter__.side_effect = aiomqtt.MqttError("broker indisponível")
    policy = ConnectionPolicy(max_retries=1, initial_delay=0.001, max_delay=0.001)

    with patch("esp32_fleet.mqtt.connection.aiomqtt.Client", return_value=unavailable) as factory:
        async with MqttConnection("esp32-001", policy=policy) as connection:
            with pytest.raises(aiomqtt.MqttError, match="broker indisponível"):
                await connection.publish_telemetry(b"{}")
        assert factory.call_count == 2


@pytest.mark.parametrize("device_id", ["", "esp32/001", "+", "x" * 65])
def test_invalid_device_id_is_rejected(device_id: str) -> None:
    """Valida o nível do tópico conforme o contrato MQTT."""
    with pytest.raises(ValueError, match="Identificador"):
        MqttConnection(device_id)
