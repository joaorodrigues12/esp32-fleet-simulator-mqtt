"""Testa telemetria e presença com um broker Mosquitto isolado."""

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiomqtt
import pytest

from esp32_fleet.domain.vitals import VitalsGenerator
from esp32_fleet.mqtt.connection import MqttConnection


@pytest.fixture
def mosquitto_executable() -> str:
    """Encontra o executável local usado pelo teste de integração."""
    configured = os.environ.get("MOSQUITTO_EXECUTABLE")
    if configured:
        assert Path(configured).is_file(), "MOSQUITTO_EXECUTABLE deve apontar para um arquivo."
        return configured
    executable = shutil.which("mosquitto")
    if executable is None:
        pytest.skip("Mosquitto ausente. Configure MOSQUITTO_EXECUTABLE para rodar a integração.")
    return executable


@contextmanager
def _running_process(
    args: list[str], log_path: Path, *, env: dict[str, str] | None = None
) -> Iterator[subprocess.Popen[bytes]]:
    # O teste termina apenas o processo isolado que iniciou.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with (
        log_path.open("ab") as log,
        subprocess.Popen(
            args,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            env=env,
        ) as process,
    ):
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@contextmanager
def _broker(executable: str, config: Path) -> Iterator[subprocess.Popen[bytes]]:
    with _running_process([executable, "-c", str(config)], config.with_suffix(".log")) as process:
        yield process


@pytest.fixture
def mosquitto_config(unused_tcp_port: int, tmp_path: Path) -> Path:
    """Isola cada instância do broker em uma porta de loopback."""
    config = tmp_path / "mosquitto.conf"
    config.write_text(
        f"listener {unused_tcp_port} 127.0.0.1\n"
        "allow_anonymous true\npersistence false\nlog_dest stdout\n",
        encoding="utf-8",
    )
    return config


async def _wait_for_broker(process: subprocess.Popen[bytes], port: int, config: Path) -> None:
    try:
        async with asyncio.timeout(5):
            while process.poll() is None:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.025)
                    continue
                writer.close()
                await writer.wait_closed()
                return
    except TimeoutError:
        pass
    log = config.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    pytest.fail(f"Mosquitto indisponível na porta de teste {port}:\n{log}")


async def _verify_recovery(executable: str, port: int, config: Path) -> None:
    device_id = "esp32-001"
    topic = f"vitals/{device_id}/data"
    generator = VitalsGenerator(seed=42)

    with ExitStack() as restarted_broker:
        async with MqttConnection(device_id, port=port, timeout=1.0) as publisher:
            with _broker(executable, config) as process:
                await _wait_for_broker(process, port, config)
                await _verify_delivery(publisher, generator, device_id, topic, port)

            # Mantém o segundo broker ativo durante a desconexão normal do publicador.
            process = restarted_broker.enter_context(_broker(executable, config))
            await _wait_for_broker(process, port, config)
            await _verify_delivery(publisher, generator, device_id, topic, port)


async def _verify_delivery(
    publisher: MqttConnection, generator: VitalsGenerator, device_id: str, topic: str, port: int
) -> None:
    async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as subscriber:
        await subscriber.subscribe(topic, qos=1)
        now = datetime.now(UTC).isoformat()
        payload = {
            "schema_version": 1,
            "message_id": str(uuid4()),
            "device_id": device_id,
            "boot_id": str(uuid4()),
            "occurred_at": now,
            "sampled_at": now,
            "sequence_number": 0,
            "session_id": None,
            "session_version": 0,
            **asdict(generator.generate()),
        }
        await publisher.publish_telemetry(json.dumps(payload).encode("utf-8"))
        message = await asyncio.wait_for(anext(subscriber.messages), timeout=5)
        assert message.topic.matches(topic)
        assert message.qos == 1
        assert isinstance(message.payload, bytes)
        assert json.loads(message.payload) == payload


def test_publish_before_and_after_mosquitto_restart(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Entrega duas amostras no tópico esperado, uma após reiniciar o Mosquitto."""
    # O aiomqtt registra leitores de socket, disponíveis no loop Selector.
    asyncio.run(
        _verify_recovery(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )


_STATUS_TOPIC = "vitals/esp32-001/status"
_COMMAND_TOPIC = "vitals/esp32-001/command"


async def _read_status(client: aiomqtt.Client, *, retained: bool = False) -> dict[str, Any]:
    message = await asyncio.wait_for(anext(client.messages), timeout=6)
    assert message.topic.matches(_STATUS_TOPIC)
    assert message.qos == 1 and message.retain == retained
    assert isinstance(message.payload, bytes)
    status = json.loads(message.payload)
    assert isinstance(status, dict)
    assert status["schema_version"] == 1 and status["device_id"] == "esp32-001"
    return status


async def _read_retained_status(port: int) -> dict[str, Any]:
    async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as newcomer:
        await newcomer.subscribe(_STATUS_TOPIC, qos=1)
        return await _read_status(newcomer, retained=True)


async def _verify_graceful_presence(executable: str, port: int, config: Path) -> None:
    commands: asyncio.Queue[aiomqtt.Message] = asyncio.Queue()
    with _broker(executable, config) as process:
        await _wait_for_broker(process, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as observer:
            await observer.subscribe(_STATUS_TOPIC, qos=1)
            async with MqttConnection(
                "esp32-001", port=port, on_command=commands.put_nowait, heartbeat_interval_seconds=1
            ) as publisher:
                online = await _read_status(observer)
                assert (online["state"], online["reason"]) == ("online", "connected")
                assert online["boot_id"] == str(publisher.boot_id)
                latest_online = await _read_retained_status(port)
                assert latest_online["state"] == "online"
                assert latest_online["boot_id"] == online["boot_id"]

                await observer.publish(_COMMAND_TOPIC, b"{}", qos=1)
                command = await asyncio.wait_for(commands.get(), timeout=3)
                assert command.topic.matches(_COMMAND_TOPIC) and command.payload == b"{}"

                heartbeat = await _read_status(observer)
                assert heartbeat["reason"] == "connected"
                assert heartbeat["boot_id"] == online["boot_id"]
                assert heartbeat["message_id"] != online["message_id"]
                assert heartbeat["heartbeat_interval_seconds"] == 1

            # Um heartbeat já confirmado pode continuar na fila quando o contexto fecha.
            async with asyncio.timeout(6):
                while (offline := await _read_status(observer))["state"] == "online":
                    assert offline["boot_id"] == online["boot_id"]
            assert (offline["state"], offline["reason"]) == ("offline", "graceful_shutdown")
            assert offline["boot_id"] == online["boot_id"]
            assert await _read_retained_status(port) == offline


def test_presence_heartbeat_commands_and_graceful_shutdown(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Verifica comandos e status retidos durante a conexão e o desligamento normal."""
    asyncio.run(
        _verify_graceful_presence(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )


_PUBLISHER_SCRIPT = """
import asyncio
import sys
from esp32_fleet.mqtt.connection import MqttConnection

async def main():
    async with MqttConnection("esp32-001", port=int(sys.argv[1]), on_command=lambda message: None):
        await asyncio.Event().wait()

asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
"""


async def _verify_last_will(executable: str, port: int, config: Path) -> None:
    with _broker(executable, config) as process:
        await _wait_for_broker(process, port, config)
        async with aiomqtt.Client("127.0.0.1", port, timeout=2.0) as observer:
            await observer.subscribe(_STATUS_TOPIC, qos=1)
            args = [sys.executable, "-c", _PUBLISHER_SCRIPT, str(port)]
            publisher_log = config.with_name("publisher.log")
            with _running_process(args, publisher_log) as publisher:
                try:
                    online = await _read_status(observer)
                except TimeoutError:
                    pytest.fail(publisher_log.read_text(encoding="utf-8", errors="replace"))
                assert (online["state"], online["reason"]) == ("online", "connected")
                # A interrupção do processo impede a publicação do offline normal.
                publisher.kill()
                publisher.wait(timeout=5)

            lost = await _read_status(observer)
            assert (lost["state"], lost["reason"]) == ("offline", "unexpected_disconnect")
            assert lost["boot_id"] == online["boot_id"]
            assert lost["message_id"] != online["message_id"]
            assert datetime.fromisoformat(lost["occurred_at"]) <= datetime.fromisoformat(
                online["occurred_at"]
            )
            assert await _read_retained_status(port) == lost


def test_mosquitto_publishes_retained_last_will_after_process_is_killed(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Confirma que o broker mantém o LWT após a interrupção do publicador."""
    asyncio.run(
        _verify_last_will(mosquitto_executable, unused_tcp_port, mosquitto_config),
        loop_factory=asyncio.SelectorEventLoop,
    )


async def _verify_authenticated_cli(executable: str, config: Path, port: int) -> None:
    tool_name = "mosquitto_passwd.exe" if sys.platform == "win32" else "mosquitto_passwd"
    sibling = Path(executable).with_name(tool_name)
    password_tool = str(sibling) if sibling.is_file() else shutil.which(tool_name)
    assert password_tool is not None, "Instale mosquitto_passwd junto com o broker de integração."

    username = "test-device"
    password = secrets.token_hex(16)
    password_file = config.with_name("passwords")
    # O modo batch recebe apenas uma senha descartável gerada para este teste.
    generated = subprocess.run(
        [password_tool, "-H", "sha512-pbkdf2", "-b", "-c", str(password_file), username, password],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert generated.returncode == 0, generated.stderr
    profile = Path(__file__).resolve().parents[2] / "docker/mosquitto/mosquitto-auth.conf"
    config.write_text(
        profile.read_text(encoding="utf-8")
        .replace("listener 1883", f"listener {port} 127.0.0.1")
        .replace("/mosquitto/data/passwords", password_file.as_posix()),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("ESP32_FLEET_MQTT_USERNAME", None)
    environment.pop("ESP32_FLEET_MQTT_PASSWORD", None)
    args = [sys.executable, "-m", "esp32_fleet", "--port", str(port), "--samples", "1", "--metrics"]

    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        for label, candidate, expected_code in (
            ("valid", password, 0),
            ("anonymous", None, 1),
            ("wrong", secrets.token_hex(16), 1),
        ):
            client_env = environment.copy()
            if candidate is not None:
                client_env["ESP32_FLEET_MQTT_USERNAME"] = username
                client_env["ESP32_FLEET_MQTT_PASSWORD"] = candidate
            log = config.with_name(f"auth-{label}.log")
            with _running_process(args, log, env=client_env) as cli:
                code = await asyncio.to_thread(cli.wait, timeout=15)
            output = log.read_text(encoding="utf-8", errors="replace")
            assert code == expected_code, output
            assert candidate is None or candidate not in output
            summaries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
            assert len(summaries) == 1, output
            assert summaries[0]["samples_acked"] == (1 if expected_code == 0 else 0)


def test_authenticated_broker_accepts_only_configured_credentials(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path
) -> None:
    """Exercita a CLI contra o perfil autenticado com três combinações de credenciais."""
    asyncio.run(
        _verify_authenticated_cli(mosquitto_executable, mosquitto_config, unused_tcp_port),
        loop_factory=asyncio.SelectorEventLoop,
    )
