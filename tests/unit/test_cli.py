"""Verifica argumentos, encerramento e identidade das amostras da CLI."""

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch
from uuid import UUID

import aiomqtt
import pytest

from esp32_fleet.__main__ import main
from esp32_fleet.config.settings import SimulatorSettings, parse_settings
from esp32_fleet.domain.vitals import VitalsScenario
from esp32_fleet.mqtt.connection import MqttConnection
from esp32_fleet.mqtt.reconnect import ConnectionPolicy
from esp32_fleet.orchestration.fleet import run_fleet


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
            "--scenario",
            "temperature-rise",
        ]
    ) == SimulatorSettings(
        "localhost", 31883, "esp32-9", 0.5, 42, 3, scenario=VitalsScenario.TEMPERATURE_RISE
    )


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--broker", " "),
        ("--port", "65536"),
        ("--interval", "0"),
        ("--interval", "nan"),
        ("--interval", "inf"),
        ("--samples", "-1"),
        ("--devices", "0"),
        ("--device-id", "esp32/+"),
        ("--scenario", "unknown"),
        ("--delay-every", "2"),
        ("--delay-seconds", "0.5"),
        ("--omit-every", "0"),
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


@pytest.mark.parametrize(
    ("every", "seconds"), [("0", "0.5"), ("2", "0"), ("2", "nan"), ("2", "inf")]
)
def test_cli_rejects_invalid_delay(every: str, seconds: str) -> None:
    """Recusa periodicidade inválida ou atraso que não possa ser aplicado."""
    with pytest.raises(SystemExit) as exc:
        parse_settings(["--delay-every", every, "--delay-seconds", seconds])
    assert exc.value.code == 2


def test_cli_maps_keyboard_interrupt_to_exit_130() -> None:
    """Informa interrupção pelo teclado com o código de saída esperado."""

    async def interrupted(settings: SimulatorSettings) -> None:
        raise KeyboardInterrupt

    with patch("esp32_fleet.__main__.run_fleet", side_effect=interrupted):
        assert main([]) == 130


@pytest.mark.parametrize("metrics_enabled", [False, True])
def test_cli_emits_partial_metrics_only_when_enabled(
    metrics_enabled: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mantém o código de saída e inclui o resumo mesmo se a conexão inicial falhar."""
    unavailable = AsyncMock(spec=aiomqtt.Client)
    unavailable.__aenter__.side_effect = aiomqtt.MqttError("broker indisponível")
    policy = ConnectionPolicy(max_retries=0)

    with (
        patch("esp32_fleet.mqtt.connection.aiomqtt.Client", return_value=unavailable),
        patch("esp32_fleet.mqtt.connection.ConnectionPolicy", return_value=policy),
    ):
        assert main(["--metrics"] if metrics_enabled else []) == 1

    result = capsys.readouterr()
    assert result.out == ""
    lines = result.err.splitlines()
    assert lines[-1] == "Falha MQTT: broker indisponível"
    if metrics_enabled:
        assert len(lines) == 2
        assert json.loads(lines[0]) == {
            "event": "telemetry_metrics",
            "device_id": "esp32-001",
            "samples_acked": 0,
            "samples_omitted": 0,
            "mqtt_errors": 1,
            "reconnections": 0,
        }
    else:
        assert len(lines) == 1


def test_cli_rejects_a_prefix_that_makes_device_ids_too_long() -> None:
    """Valida o tamanho dos IDs completos antes de criar conexões."""
    with pytest.raises(SystemExit) as exc:
        parse_settings(["--devices", "2", "--device-id", "a" * 61])
    assert exc.value.code == 2


@pytest.mark.asyncio
async def test_fleet_assigns_distinct_ids_and_reproducible_seeds() -> None:
    """Mantém as opções compartilhadas e cria uma configuração por membro."""
    settings = parse_settings(
        ["--devices", "3", "--device-id", "frota", "--seed", "-42", "--samples", "2"]
    )

    with patch("esp32_fleet.orchestration.fleet.run_device") as worker:
        await run_fleet(settings)

    members = sorted(
        (call.args[0] for call in worker.await_args_list), key=lambda member: member.device_id
    )
    assert [member.device_id for member in members] == ["frota-001", "frota-002", "frota-003"]
    assert [member.seed for member in members] == [42, 43, 44]
    assert all(member.devices == 1 and member.samples == 2 for member in members)


@pytest.mark.asyncio
async def test_fleet_reports_failed_member_after_the_others_finish() -> None:
    """Deixa um membro saudável terminar mesmo após a falha MQTT de outro."""
    completed: list[str] = []

    async def run_member(settings: SimulatorSettings) -> None:
        if settings.device_id == "esp32-001":
            raise aiomqtt.MqttError("tentativas esgotadas")
        await asyncio.sleep(0)
        completed.append(settings.device_id)

    with (
        patch("esp32_fleet.orchestration.fleet.run_device", side_effect=run_member),
        pytest.raises(aiomqtt.MqttError, match="1 de 2 dispositivos"),
    ):
        await run_fleet(parse_settings(["--devices", "2", "--samples", "1"]))

    assert completed == ["esp32-002"]


@pytest.mark.asyncio
async def test_cancelling_fleet_waits_for_every_member_to_close() -> None:
    """Propaga o cancelamento e aguarda a limpeza de todos os membros."""
    started: asyncio.Queue[str] = asyncio.Queue()
    stopped: set[str] = set()

    async def run_member(settings: SimulatorSettings) -> None:
        started.put_nowait(settings.device_id)
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            stopped.add(settings.device_id)

    async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:
        with patch("esp32_fleet.orchestration.fleet.run_device", side_effect=run_member):
            fleet = tasks.create_task(run_fleet(parse_settings(["--devices", "2"])))
            members = {await started.get(), await started.get()}
            fleet.cancel()
            with pytest.raises(asyncio.CancelledError):
                await fleet
    assert stopped == members == {"esp32-001", "esp32-002"}


@pytest.mark.asyncio
async def test_publish_sample_replays_original_bytes_and_uses_new_boot_next() -> None:
    """A leitura atrasada mantém o boot de origem e a próxima usa o novo boot."""

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
    simulated_delay = 0.25
    original_sleep = asyncio.sleep
    delayed_samples = 0

    def sample(boot_id: UUID) -> bytes:
        sampled_boots.append(boot_id)
        return str(boot_id).encode("ascii")

    async def sleep(seconds: float) -> None:
        nonlocal delayed_samples
        if seconds == simulated_delay:
            assert len(sampled_boots) == 1
            assert first.publish.await_count == 1  # Apenas o anúncio de presença saiu.
            delayed_samples += 1
        else:
            await original_sleep(seconds)

    topic = "vitals/esp32-001/data"
    with (
        patch("esp32_fleet.mqtt.connection.aiomqtt.Client", side_effect=[first, second]),
        patch("esp32_fleet.mqtt.connection.asyncio.sleep", side_effect=sleep),
    ):
        async with MqttConnection(
            "esp32-001", policy=policy, on_command=lambda message: None
        ) as connection:
            await connection.publish_sample(sample, delay_seconds=simulated_delay)
            await connection.publish_sample(sample)

    assert delayed_samples == 1
    original, recovered = sampled_boots
    assert original != recovered
    assert first.publish.await_args_list[1].args == (topic, str(original).encode("ascii"))
    assert second.publish.await_args_list[1].args == (topic, str(original).encode("ascii"))
    assert second.publish.await_args_list[2].args == (topic, str(recovered).encode("ascii"))
