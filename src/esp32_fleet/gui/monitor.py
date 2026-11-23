"""Acompanha os anúncios de presença da frota em uma conexão MQTT de leitura."""

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal, Self
from uuid import UUID

import aiomqtt
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from PySide6.QtCore import QObject, QThread, Signal

from esp32_fleet.config.settings import SimulatorSettings

type MqttState = Literal["online", "offline"]


class _StatusEnvelope(BaseModel):
    """Campos obrigatórios do contrato DeviceStatusV1."""

    model_config = ConfigDict(strict=True)

    schema_version: int = Field(ge=1, le=1)
    message_id: UUID
    device_id: str
    boot_id: UUID
    occurred_at: AwareDatetime
    state: MqttState
    reason: Literal["connected", "graceful_shutdown", "unexpected_disconnect"]
    heartbeat_interval_seconds: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class MonitorCredentials:
    """Credenciais de leitura, com opção de usar a conta do simulador."""

    username: str | None
    password: str | None = field(repr=False)

    @classmethod
    def resolve(
        cls, settings: SimulatorSettings, username: str | None, password: str | None
    ) -> Self:
        """Valida o par exclusivo do monitor ou usa a configuração do ensaio."""
        if bool(username) != bool(password):
            raise ValueError("Defina o usuário e a senha MQTT do monitor em conjunto.")
        if username:
            return cls(username, password)
        return cls(settings.username, settings.password)


def status_from_message(
    settings: SimulatorSettings, topic: str, payload: object
) -> tuple[str, MqttState] | None:
    """Aceita anúncios válidos cujo tópico e ID pertencem à frota configurada."""
    levels = topic.split("/")
    if len(levels) != 3 or levels[0] != "vitals" or levels[2] != "status":
        return None
    device_id = levels[1]
    if settings.devices == 1:
        if settings.device_id != device_id:
            return None
    else:
        prefix = f"{settings.device_id}-"
        number = device_id.removeprefix(prefix)
        if (
            not device_id.startswith(prefix)
            or len(number) != settings.device_number_width
            or not number.isascii()
            or not number.isdecimal()
            or not 1 <= int(number) <= settings.devices
        ):
            return None

    if not isinstance(payload, bytes) or not 0 < len(payload) <= 16 * 1024:
        return None
    try:
        status = _StatusEnvelope.model_validate_json(payload)
    except ValidationError:
        return None
    if (
        status.device_id != device_id
        or status.occurred_at.utcoffset() != timedelta(0)
        or (status.state == "online") != (status.reason == "connected")
    ):
        return None
    return device_id, status.state


class FleetMonitor(QThread):
    """Mantém a conexão de leitura fora da thread que desenha a janela."""

    connection_changed = Signal(bool)
    status_received = Signal(str, str)

    def __init__(
        self, settings: SimulatorSettings, credentials: MonitorCredentials, parent: QObject
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._credentials = credentials
        self._stop_signal: Future[None] = Future()

    def stop(self) -> None:
        """Cancela a escuta e qualquer espera de reconexão sem bloquear o Qt."""
        self._stop_signal.cancel()

    def run(self) -> None:
        """Cria um event loop compatível com aiomqtt também no Windows."""
        asyncio.run(self._run_until_stopped(), loop_factory=asyncio.SelectorEventLoop)

    async def _run_until_stopped(self) -> None:
        if self._stop_signal.cancelled():
            return
        stop_signal = asyncio.wrap_future(self._stop_signal)
        listener = asyncio.create_task(self._listen())
        try:
            await asyncio.wait((stop_signal, listener), return_when=asyncio.FIRST_COMPLETED)
            if listener.done():
                await listener
        finally:
            stop_signal.cancel()
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    async def _listen(self) -> None:
        settings = self._settings
        topic = (
            f"vitals/{settings.device_id}/status" if settings.devices == 1 else "vitals/+/status"
        )
        while True:
            try:
                async with aiomqtt.Client(
                    settings.broker,
                    settings.port,
                    username=self._credentials.username,
                    password=self._credentials.password,
                    tls_context=settings.tls_context,
                    protocol=aiomqtt.ProtocolVersion.V311,
                    keepalive=5,
                    timeout=3,
                ) as client:
                    granted = await client.subscribe(topic, qos=1)
                    if len(granted) != 1 or granted[0] != 1:
                        raise aiomqtt.MqttError(
                            "Assinatura de status MQTT sem confirmação de QoS 1."
                        )
                    self.connection_changed.emit(True)
                    async for message in client.messages:
                        status = status_from_message(settings, message.topic.value, message.payload)
                        if status is not None:
                            self.status_received.emit(*status)
            except (aiomqtt.MqttError, OSError):
                self.connection_changed.emit(False)
                await asyncio.sleep(1)
