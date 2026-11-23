"""Valida identidade TLS e permissões por tópico contra um Mosquitto isolado."""

import asyncio
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

import aiomqtt
import pytest
import trustme
from test_mqtt_connection_integration import _broker, _running_process, _wait_for_broker
from test_mqtt_connection_integration import mosquitto_config as mosquitto_config
from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable


def _configure_broker(
    executable: str, config: Path, port: int, certificate_identity: str
) -> tuple[Path, dict[str, str]]:
    """Instala certificados efêmeros e as duas contas para o perfil versionado."""
    ca = trustme.CA()
    cert = ca.issue_cert(certificate_identity)
    ca_path = config.with_name("client.pem")
    cert_path = config.with_name("server.pem")
    key_path = config.with_name("server.key")
    ca.cert_pem.write_to_path(ca_path)
    cert.cert_chain_pems[0].write_to_path(cert_path)
    cert.private_key_pem.write_to_path(key_path)

    tool_name = "mosquitto_passwd.exe" if sys.platform == "win32" else "mosquitto_passwd"
    sibling = Path(executable).with_name(tool_name)
    password_tool = str(sibling) if sibling.is_file() else shutil.which(tool_name)
    assert password_tool is not None, "Instale mosquitto_passwd junto com o broker de integração."
    passwords = config.with_name("passwords")
    accounts = {username: secrets.token_hex(16) for username in ("simulator", "controller")}
    for username, password in accounts.items():
        flags = ["-c"] if not passwords.exists() else []
        # As senhas aleatórias existem apenas durante este teste de integração.
        result = subprocess.run(
            [
                password_tool,
                "-H",
                "sha512-pbkdf2",
                "-b",
                *flags,
                str(passwords),
                username,
                password,
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr

    profile = Path(__file__).resolve().parents[2] / "docker/mosquitto/mosquitto-tls.conf"
    acl = profile.with_name("mosquitto.acl")
    config.write_text(
        profile.read_text(encoding="utf-8")
        .replace("listener 8883", f"listener {port} 127.0.0.1")
        .replace("/mosquitto/data/server.pem", cert_path.as_posix())
        .replace("/mosquitto/data/server.key", key_path.as_posix())
        .replace("/mosquitto/data/passwords", passwords.as_posix())
        .replace("/mosquitto/config/mosquitto.acl", acl.as_posix()),
        encoding="utf-8",
    )
    return ca_path, accounts


async def _verify_client_tls(
    port: int, ca_path: Path, password: str, expected_error: str | None
) -> None:
    environment = {
        **os.environ,
        "ESP32_FLEET_MQTT_USERNAME": "simulator",
        "ESP32_FLEET_MQTT_PASSWORD": password,
        "ESP32_FLEET_MQTT_CA_FILE": str(ca_path),
    }
    args = [sys.executable, "-m", "esp32_fleet", "--port", str(port), "--samples", "1", "--metrics"]
    log = ca_path.with_name("tls-cli.log")
    with _running_process(args, log, env=environment) as cli:
        code = await asyncio.to_thread(cli.wait, timeout=15)
    output = log.read_text(encoding="utf-8", errors="replace")
    summaries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(summaries) == 1, output
    if expected_error is None:
        assert code == 0, output
        assert summaries[0]["samples_acked"] == 1
    else:
        assert code == 1, output
        assert expected_error in output.lower(), output
        assert summaries[0]["samples_acked"] == 0


async def _verify_topic_permissions(port: int, ca_path: Path, accounts: dict[str, str]) -> None:
    tls = ssl.create_default_context(cafile=ca_path)
    command_topic = "vitals/acl-device/command"
    data_topic = "vitals/acl-device/data"
    async with (
        aiomqtt.Client(
            "127.0.0.1",
            port,
            username="simulator",
            password=accounts["simulator"],
            tls_context=tls,
            protocol=aiomqtt.ProtocolVersion.V311,
            timeout=3,
        ) as simulator,
        aiomqtt.Client(
            "127.0.0.1",
            port,
            username="controller",
            password=accounts["controller"],
            tls_context=tls,
            protocol=aiomqtt.ProtocolVersion.V311,
            timeout=3,
        ) as controller,
    ):
        await simulator.subscribe(command_topic, qos=1)
        await controller.subscribe(data_topic, qos=1)

        # No MQTT 3.1.1, PUBACK pode ser enviado mesmo se a ACL impedir a entrega.
        # Cada mensagem permitida serve de referência após a publicação bloqueada.
        await simulator.publish(command_topic, b"simulator-blocked", qos=1)
        await simulator.publish(data_topic, b"simulator-allowed", qos=1)
        data = await asyncio.wait_for(anext(controller.messages), timeout=3)
        assert data.topic.matches(data_topic) and data.payload == b"simulator-allowed"

        await controller.publish(data_topic, b"controller-blocked", qos=1)
        await controller.publish(command_topic, b"controller-allowed", qos=1)
        command = await asyncio.wait_for(anext(simulator.messages), timeout=3)
        assert command.topic.matches(command_topic) and command.payload == b"controller-allowed"

        await simulator.publish(data_topic, b"simulator-confirmed", qos=1)
        data = await asyncio.wait_for(anext(controller.messages), timeout=3)
        assert data.topic.matches(data_topic) and data.payload == b"simulator-confirmed"


async def _verify_tls_and_acl(
    executable: str,
    config: Path,
    port: int,
    certificate_identity: str,
    trusted_ca: bool,
    expected_error: str | None,
) -> None:
    ca_path, accounts = _configure_broker(executable, config, port, certificate_identity)
    if not trusted_ca:
        ca_path = ca_path.with_name("unknown-ca.pem")
        trustme.CA().cert_pem.write_to_path(ca_path)
    with _broker(executable, config) as broker:
        await _wait_for_broker(broker, port, config)
        await _verify_client_tls(port, ca_path, accounts["simulator"], expected_error)
        if expected_error is None:
            await _verify_topic_permissions(port, ca_path, accounts)


@pytest.mark.parametrize(
    ("certificate_identity", "trusted_ca", "expected_error"),
    [
        pytest.param("127.0.0.1", True, None, id="trusted-tls-and-acl"),
        pytest.param("127.0.0.1", False, "unable to get local issuer certificate", id="unknown-ca"),
        # Mantém a mesma porta IPv4 e varia apenas a identidade do certificado.
        pytest.param("localhost", True, "ip address mismatch", id="host-mismatch"),
    ],
)
def test_tls_and_topic_permissions(
    mosquitto_executable: str,
    unused_tcp_port: int,
    mosquitto_config: Path,
    certificate_identity: str,
    trusted_ca: bool,
    expected_error: str | None,
) -> None:
    """Confirma o certificado do broker e testa a entrega permitida por papel."""
    asyncio.run(
        _verify_tls_and_acl(
            mosquitto_executable,
            mosquitto_config,
            unused_tcp_port,
            certificate_identity,
            trusted_ca,
            expected_error,
        ),
        loop_factory=asyncio.SelectorEventLoop,
    )
