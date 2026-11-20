"""Testa a telemetria com um broker Mosquitto isolado e reiniciável."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
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
def _broker(executable: str, config: Path) -> Iterator[subprocess.Popen[bytes]]:
    # O teste gerencia apenas o processo que iniciou, em uma porta exclusiva.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with (
        config.with_suffix(".log").open("ab") as log,
        subprocess.Popen(
            [executable, "-c", str(config)],
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
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
    mosquitto_executable: str, unused_tcp_port: int, tmp_path: Path
) -> None:
    """Entrega duas amostras no tópico esperado, uma após reiniciar o Mosquitto."""
    config = tmp_path / "mosquitto.conf"
    config.write_text(
        f"listener {unused_tcp_port} 127.0.0.1\n"
        "allow_anonymous true\npersistence false\nlog_dest stdout\n",
        encoding="utf-8",
    )
    # O aiomqtt registra leitores de socket, disponíveis no loop Selector.
    asyncio.run(
        _verify_recovery(mosquitto_executable, unused_tcp_port, config),
        loop_factory=asyncio.SelectorEventLoop,
    )
