"""Verifica a política de reconexão e a publicação MQTT sem um broker."""

import json
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


@pytest.mark.asyncio
async def test_presence_announces_online_after_suback_and_offline_before_disconnect() -> None:
    """Verifica a ordem do anúncio e a identidade compartilhada com o Last Will."""
    client = AsyncMock(spec=aiomqtt.Client)
    client.__aenter__.return_value = client
    client.subscribe.return_value = (1,)

    with patch("esp32_fleet.mqtt.connection.aiomqtt.Client", return_value=client) as factory:
        async with MqttConnection("esp32-001", on_command=lambda message: None) as connection:
            boot_id = connection.boot_id
            assert boot_id is not None
            assert [call[0] for call in client.method_calls[:2]] == ["subscribe", "publish"]

    assert connection.boot_id is None
    client.subscribe.assert_awaited_once_with("vitals/esp32-001/command", qos=1)
    assert client.mock_calls[-1][0] == "__aexit__"
    online_call, offline_call = client.publish.await_args_list
    will = factory.call_args.kwargs["will"]
    online, offline, lost = (
        json.loads(online_call.args[1]),
        json.loads(offline_call.args[1]),
        json.loads(will.payload),
    )
    assert online_call.args[0] == offline_call.args[0] == will.topic == "vitals/esp32-001/status"
    assert online_call.kwargs == offline_call.kwargs == {"qos": 1, "retain": True}
    assert will.qos == 1 and will.retain is True
    assert (online["state"], online["reason"]) == ("online", "connected")
    assert (offline["state"], offline["reason"]) == ("offline", "graceful_shutdown")
    assert (lost["state"], lost["reason"]) == ("offline", "unexpected_disconnect")
    assert online["boot_id"] == offline["boot_id"] == lost["boot_id"] == str(boot_id)
    assert len({status["message_id"] for status in (online, offline, lost)}) == 3


@pytest.mark.asyncio
async def test_presence_stays_unannounced_when_subscription_is_rejected() -> None:
    """Evita anunciar disponibilidade quando o broker rejeita a assinatura."""
    client = AsyncMock(spec=aiomqtt.Client)
    client.__aenter__.return_value = client
    client.subscribe.return_value = (128,)
    connection = MqttConnection(
        "esp32-001", policy=ConnectionPolicy(max_retries=0), on_command=lambda message: None
    )

    with (
        patch("esp32_fleet.mqtt.connection.aiomqtt.Client", return_value=client),
        pytest.raises(aiomqtt.MqttError, match="assinatura"),
    ):
        async with connection:
            pytest.fail("A entrada no contexto deveria falhar.")

    assert connection.boot_id is None
    client.publish.assert_not_awaited()
    client.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_presence_renews_boot_id_when_publication_reconnects() -> None:
    """Verifica que o novo cliente tem outro boot_id no Will e no status."""
    first = AsyncMock(spec=aiomqtt.Client)
    first.__aenter__.return_value = first
    first.subscribe.return_value = (1,)
    first.publish.side_effect = [None, aiomqtt.MqttError("conexão perdida")]
    second = AsyncMock(spec=aiomqtt.Client)
    second.__aenter__.return_value = second
    second.subscribe.return_value = (1,)
    policy = ConnectionPolicy(max_retries=1, initial_delay=0.001, max_delay=0.001)

    with patch(
        "esp32_fleet.mqtt.connection.aiomqtt.Client", side_effect=[first, second]
    ) as factory:
        async with MqttConnection(
            "esp32-001", policy=policy, on_command=lambda message: None
        ) as connection:
            original = connection.boot_id
            await connection.publish_telemetry(b"{}")
            recovered = connection.boot_id

    assert original is not None and recovered is not None and original != recovered
    for client, call, boot_id in zip(
        (first, second), factory.call_args_list, (original, recovered), strict=True
    ):
        assert json.loads(call.kwargs["will"].payload)["boot_id"] == str(boot_id)
        assert json.loads(client.publish.await_args_list[0].args[1])["boot_id"] == str(boot_id)
