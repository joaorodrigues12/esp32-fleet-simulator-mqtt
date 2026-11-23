"""Exercita o consumidor de referência com telemetria publicada via Mosquitto."""

import asyncio
import json
import os
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiomqtt
import pytest
from test_mqtt_connection_integration import _broker, _running_process, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable
from test_mqtt_tls_integration import _configure_broker as _configure_tls_broker

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "consume_telemetry.py"
_TOPIC = "vitals/esp32-001/data"


async def _publish_readings(
    publisher: aiomqtt.Client, first: bytes, second: bytes, third: bytes
) -> None:
    invalid_readings = (
        (_TOPIC, b"{"),
        ("vitals/esp32-002/data", first),
        (_TOPIC, first.replace(b'"schema_version": 1', b'"schema_version": 2')),
        (_TOPIC, first.replace(b'"temperature_c": 36.7', b'"temperature_c": 44.0')),
        (_TOPIC, first + b" " * (16 * 1024)),
    )
    for topic, payload in invalid_readings:
        await publisher.publish(topic, payload, qos=1)
    await publisher.publish(_TOPIC, first, qos=0)

    # A terceira leitura inédita libera a vaga da primeira na janela de dois IDs.
    for payload in (first, first, second, first, third, first):
        await publisher.publish(_TOPIC, payload, qos=1)


async def _verify_consumer(executable: str, port: int, config: Path, with_tls: bool) -> None:
    client_env = {
        key: value for key, value in os.environ.items() if not key.startswith("ESP32_FLEET_MQTT_")
    }
    client_env["PYTHONUTF8"] = "1"
    publish_username = None
    publish_password = None
    tls_context = None
    if with_tls:
        ca_path, accounts = _configure_tls_broker(executable, config, port, "127.0.0.1")
        client_env.update(
            ESP32_FLEET_MQTT_USERNAME="controller",
            ESP32_FLEET_MQTT_PASSWORD=accounts["controller"],
            ESP32_FLEET_MQTT_CA_FILE=str(ca_path),
        )
        publish_username = "simulator"
        publish_password = accounts["simulator"]
        tls_context = ssl.create_default_context(cafile=ca_path)

    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    reading = {
        "schema_version": 1,
        "message_id": str(uuid4()),
        "device_id": "esp32-001",
        "boot_id": str(uuid4()),
        "occurred_at": timestamp,
        "sampled_at": timestamp,
        "sequence_number": 0,
        "session_id": str(uuid4()),
        "session_version": 1,
        "heart_rate_bpm": 82,
        "respiratory_rate_rpm": 17,
        "temperature_c": 36.7,
    }
    samples = [
        {**reading, "message_id": str(uuid4()), "sequence_number": index} for index in range(3)
    ]
    first, second, third = (json.dumps(sample).encode("utf-8") for sample in samples)
    log = config.with_name("consumer.log")
    args = [
        sys.executable,
        str(_SCRIPT),
        "--port",
        str(port),
        "--messages",
        "4",
        "--dedup-window",
        "2",
    ]
    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        async with aiomqtt.Client(
            "127.0.0.1",
            port,
            username=publish_username,
            password=publish_password,
            tls_context=tls_context,
            protocol=aiomqtt.ProtocolVersion.V311,
        ) as publisher:
            await publisher.publish(_TOPIC, json.dumps(reading), qos=1, retain=True)
            with _running_process(args, log, env=client_env) as consumer:
                async with asyncio.timeout(8):
                    while "Assinatura ativa em vitals/+/data." not in log.read_text(
                        encoding="utf-8", errors="replace"
                    ):
                        assert consumer.poll() is None, log.read_text(encoding="utf-8")
                        await asyncio.sleep(0.02)
                await _publish_readings(publisher, first, second, third)
                code = await asyncio.to_thread(consumer.wait, timeout=10)

    output = log.read_text(encoding="utf-8")
    assert code == 0, output
    accepted = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert accepted == [*samples, samples[0]], output
    assert output.count("Leitura descartada pelo contrato VitalsDataV1.") == 7, output


@pytest.mark.parametrize("with_tls", [False, True], ids=["local", "tls"])
def test_consumer_validates_deduplicates_and_stops_at_reading_limit(
    mosquitto_executable: str, unused_tcp_port: int, mosquitto_config: Path, with_tls: bool
) -> None:
    """Confirma o contrato e a janela de deduplicação com e sem TLS."""
    asyncio.run(
        _verify_consumer(mosquitto_executable, unused_tcp_port, mosquitto_config, with_tls),
        loop_factory=asyncio.SelectorEventLoop,
    )
