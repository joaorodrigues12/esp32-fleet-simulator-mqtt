"""Verifica argumentos, encerramento e identidade das amostras da CLI."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID

import aiomqtt
import pytest

from esp32_fleet.__main__ import main
from esp32_fleet.config.settings import SimulatorSettings, parse_settings
from esp32_fleet.mqtt.connection import MqttConnection
from esp32_fleet.mqtt.reconnect import ConnectionPolicy


def test_cli_defaults_and_custom_settings() -> None:
    """Interpreta as opções de conexão e execução."""
    assert parse_settings([]) == SimulatorSettings("127.0.0.1", 1883, "esp32-001", 2.0, None, None)
    assert parse_settings(
        [
            "--broker",
            "localhost",
            "--port",
            "31883",
            "--device-id",
            "esp32-9",
            "--interval",
            "0.5",
            "--seed",
            "42",
            "--samples",
            "3",
        ]
    ) == SimulatorSettings("localhost", 31883, "esp32-9", 0.5, 42, 3)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--broker", " "),
        ("--port", "65536"),
        ("--interval", "0"),
        ("--interval", "nan"),
        ("--interval", "inf"),
        ("--samples", "-1"),
    ],
)
def test_cli_rejects_invalid_settings(
    option: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rejeita argumentos inválidos antes de abrir o broker."""
    with pytest.raises(SystemExit) as exc:
        parse_settings([option, value])
    assert exc.value.code == 2
    assert option in capsys.readouterr().err


def test_cli_maps_keyboard_interrupt_to_exit_130() -> None:
    """Informa interrupção pelo teclado com o código de saída esperado."""

    async def interrupted(settings: SimulatorSettings) -> None:
        raise KeyboardInterrupt

    with patch("esp32_fleet.__main__.run_device", side_effect=interrupted):
        assert main([]) == 130


@pytest.mark.asyncio
async def test_publish_sample_replays_original_bytes_and_uses_new_boot_next() -> None:
    """Uma leitura em trânsito mantém o boot de origem e a próxima usa o novo boot."""

    async def messages() -> AsyncIterator[aiomqtt.Message]:
        queue: asyncio.Queue[aiomqtt.Message] = asyncio.Queue()
        while True:
            yield await queue.get()

    first, second = (AsyncMock(spec=aiomqtt.Client) for _ in range(2))
    for client in (first, second):
        client.__aenter__.return_value = client
        client.subscribe.return_value = (1,)
        client.messages = messages()
    first.publish.side_effect = [None, aiomqtt.MqttError("publicação sem confirmação")]
    policy = ConnectionPolicy(max_retries=1, initial_delay=0.001, max_delay=0.001)
    sampled_boots: list[UUID] = []

    def sample(boot_id: UUID) -> bytes:
        sampled_boots.append(boot_id)
        return str(boot_id).encode("ascii")

    topic = "vitals/esp32-001/data"
    with patch("esp32_fleet.mqtt.connection.aiomqtt.Client", side_effect=[first, second]):
        async with MqttConnection(
            "esp32-001", policy=policy, on_command=lambda message: None
        ) as connection:
            await connection.publish_sample(sample)
            await connection.publish_sample(sample)

    original, recovered = sampled_boots
    assert original != recovered
    assert first.publish.await_args_list[1].args == (topic, str(original).encode("ascii"))
    assert second.publish.await_args_list[1].args == (topic, str(original).encode("ascii"))
    assert second.publish.await_args_list[2].args == (topic, str(recovered).encode("ascii"))
