"""Gerencia telemetria e presença de um dispositivo virtual em MQTT 3.1.1."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import AsyncExitStack, suppress
from math import isfinite
from types import TracebackType
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

import aiomqtt

from esp32_fleet.mqtt.presence import DevicePresence
from esp32_fleet.mqtt.reconnect import ConnectionPolicy

if TYPE_CHECKING:
    from esp32_fleet.handlers.commands import CommandReplies
    from esp32_fleet.observability.metrics import TelemetryMetrics

_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_LOGGER = logging.getLogger(__name__)


class MqttConnection:
    """Reutiliza uma conexão MQTT por dispositivo dentro de um contexto assíncrono."""

    def __init__(
        self,
        device_id: str,
        *,
        hostname: str = "127.0.0.1",
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        policy: ConnectionPolicy | None = None,
        timeout: float = 3.0,
        on_command: Callable[[aiomqtt.Message], None] | None = None,
        on_session_command: Callable[[bytes, UUID], CommandReplies | None] | None = None,
        heartbeat_interval_seconds: int = 10,
        metrics: TelemetryMetrics | None = None,
    ) -> None:
        """Configura a publicação de telemetria para um dispositivo.

        Args:
            device_id: Identificador operacional usado no tópico e no cliente MQTT.
            hostname: Host do broker.
            port: Porta TCP do broker.
            username: Usuário MQTT usado em cada conexão.
            password: Senha MQTT usada em cada conexão.
            policy: Política de reconexão usada em cada publicação.
            timeout: Tempo em segundos para aguardar confirmações do protocolo.
            on_command: Callback síncrono para encaminhar comandos. Fornecê-lo
                habilita a assinatura de comandos e os anúncios de presença.
            on_session_command: Callback que processa um comando e prepara o ACK
                e o evento de sessão. Também habilita presença.
            heartbeat_interval_seconds: Intervalo entre anúncios de presença.
            metrics: Contadores opcionais compartilhados com o ciclo do dispositivo.

        Raises:
            ValueError: Se a configuração for inválida ou ambos os callbacks forem fornecidos.
        """
        if not _DEVICE_ID.fullmatch(device_id):
            raise ValueError("Identificador de dispositivo MQTT inválido.")
        if not 1 <= port <= 65535:
            raise ValueError("A porta MQTT deve estar entre 1 e 65535.")
        if not (isfinite(timeout) and timeout > 0):
            raise ValueError("O timeout MQTT deve ser positivo e finito.")
        if type(heartbeat_interval_seconds) is not int or heartbeat_interval_seconds < 1:
            raise ValueError("O intervalo de presença deve ser um inteiro positivo.")
        if on_command is not None and on_session_command is not None:
            raise ValueError("Configure um único callback de comandos.")
        self._device_id = device_id
        self._topic = f"vitals/{device_id}/data"
        self._hostname = hostname
        self._port = port
        self._username = username
        self._password = password
        self._policy = policy if policy is not None else ConnectionPolicy()
        self._timeout = timeout
        self._on_command = on_command
        self._on_session_command = on_session_command
        self._presence_enabled = on_command is not None or on_session_command is not None
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._metrics = metrics
        self._lock = asyncio.Lock()
        self._client_scope = AsyncExitStack()
        self._client: aiomqtt.Client | None = None
        self._presence: DevicePresence | None = None
        self._presence_tasks: tuple[asyncio.Task[None], ...] = ()
        self._active = False

    @property
    def boot_id(self) -> UUID | None:
        """Retorna o identificador da conexão que anunciou presença, se disponível."""
        return self._presence.boot_id if self._presence is not None else None

    async def __aenter__(self) -> Self:
        """Abre o escopo de uso. Quando habilitada, anuncia presença na entrada."""
        async with self._lock:
            if self._active:
                raise RuntimeError("A conexão MQTT já está em uso.")
            if self._presence_enabled:
                await self._connect_and_publish()
            self._active = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Encerra o cliente ativo e impede novas publicações nesse contexto."""
        async with self._lock:
            self._active = False
            try:
                await self._stop_presence_tasks()
                if self._presence is not None and self._client is not None:
                    await self._presence.publish(self._client, "graceful_shutdown")
            except aiomqtt.MqttError:
                self._record_mqtt_error()
                # Preserva uma exceção do chamador e registra a falha do anúncio final.
                if exc is None:
                    raise
                _LOGGER.warning("Falha ao confirmar o status offline de %s.", self._device_id)
            finally:
                await self._disconnect()

    async def publish_telemetry(self, payload: bytes) -> None:
        """Publica bytes JSON do contrato com QoS 1 e sem retenção.

        Args:
            payload: Mensagem serializada para o dispositivo. Todas as tentativas
                reutilizam os mesmos bytes, inclusive o identificador da mensagem.

        Raises:
            ValueError: Se a mensagem estiver vazia ou exceder 16 KiB.
            RuntimeError: Se a publicação ocorrer fora do contexto assíncrono.
            aiomqtt.MqttError: Se a publicação falhar após as tentativas configuradas.
        """
        _validate_telemetry_payload(payload)
        async with self._lock:
            if not self._active:
                raise RuntimeError("Abra o contexto MQTT antes de publicar.")
            await self._connect_and_publish(payload)

    async def publish_sample(
        self, sample: Callable[[UUID], bytes], *, delay_seconds: float = 0.0
    ) -> None:
        """Obtém uma amostra no boot conectado e preserva os bytes nas novas tentativas.

        Args:
            sample: Função síncrona que serializa a leitura para o boot informado.
            delay_seconds: Espera antes da primeira tentativa de envio, após a serialização.

        Raises:
            RuntimeError: Se o contexto com presença não estiver ativo.
            ValueError: Se o atraso for inválido ou a mensagem estiver fora dos limites.
            aiomqtt.MqttError: Se as tentativas de conexão ou publicação falharem.
        """
        if not isfinite(delay_seconds) or delay_seconds < 0:
            raise ValueError("O atraso da amostra deve ser finito e não negativo.")
        async with self._lock:
            if not self._active or not self._presence_enabled:
                raise RuntimeError("Abra o contexto MQTT com presença antes de gerar amostras.")
            await self._connect_and_publish(sample=sample, sample_delay=delay_seconds)

    async def _connect_and_publish(
        self,
        payload: bytes | None = None,
        *,
        sample: Callable[[UUID], bytes] | None = None,
        sample_delay: float = 0.0,
    ) -> None:
        delays = self._policy.retry_delays()
        while True:
            try:
                if any(task.done() for task in self._presence_tasks):
                    await self._disconnect()
                client = self._client if self._client is not None else await self._connect()
                if payload is None and sample is not None:
                    boot_id = self.boot_id
                    if boot_id is None:
                        raise RuntimeError("A conexão MQTT não anunciou presença.")
                    payload = sample(boot_id)
                    _validate_telemetry_payload(payload)
                    if sample_delay:
                        # O mesmo payload segue para as tentativas posteriores sem repetir a espera.
                        await asyncio.sleep(sample_delay)
                if payload is not None:
                    await client.publish(self._topic, payload, qos=1, retain=False)
                    if self._metrics is not None:
                        self._metrics.samples_acked += 1
                return
            except aiomqtt.MqttError:
                self._record_mqtt_error()
                await self._disconnect()
                delay = next(delays, None)
                if delay is None:
                    raise
            except asyncio.CancelledError:
                # O contexto ativo ainda pode anunciar offline no encerramento por SIGINT.
                if self._client is None:
                    await self._disconnect()
                raise
            await asyncio.sleep(delay)

    async def _connect(self) -> aiomqtt.Client:
        presence = (
            DevicePresence(self._device_id, uuid4(), self._heartbeat_interval_seconds)
            if self._presence_enabled
            else None
        )
        client = await self._client_scope.enter_async_context(
            aiomqtt.Client(
                self._hostname,
                self._port,
                identifier=f"esp32-fleet-{self._device_id}",
                username=self._username,
                password=self._password,
                protocol=aiomqtt.ProtocolVersion.V311,
                clean_session=True,
                timeout=self._timeout,
                will=presence.will() if presence is not None else None,
            )
        )
        if presence is not None:
            granted = await client.subscribe(f"vitals/{self._device_id}/command", qos=1)
            if len(granted) != 1 or granted[0] != 1:
                raise aiomqtt.MqttError(
                    "O broker não confirmou a assinatura de comandos com QoS 1."
                )
            await presence.publish(client, "connected")
            self._presence = presence
            self._presence_tasks = (
                asyncio.create_task(self._forward_commands(client, presence.boot_id)),
                asyncio.create_task(self._send_heartbeats(client, presence)),
            )
        self._client = client
        if self._metrics is not None:
            self._metrics.record_connection()
        return client

    def _record_mqtt_error(self) -> None:
        if self._metrics is not None:
            self._metrics.mqtt_errors += 1

    async def _forward_commands(self, client: aiomqtt.Client, boot_id: UUID) -> None:
        try:
            async for message in client.messages:
                try:
                    if self._on_command is not None:
                        self._on_command(message)
                        continue
                    if self._on_session_command is None or not isinstance(message.payload, bytes):
                        continue
                    if message.retain:
                        _LOGGER.warning("Comando retido ignorado para %s.", self._device_id)
                        continue
                    replies = self._on_session_command(message.payload, boot_id)
                except Exception:
                    _LOGGER.exception("Falha ao encaminhar um comando de %s.", self._device_id)
                    continue
                if replies is not None:
                    # As respostas usam exclusivamente o cliente que recebeu o comando.
                    for channel, payload in (("ack", replies.ack), ("session", replies.event)):
                        if payload is None:
                            continue
                        try:
                            await client.publish(
                                f"vitals/{self._device_id}/{channel}", payload, qos=1, retain=False
                            )
                        except aiomqtt.MqttError:
                            self._record_mqtt_error()
                            # O controlador pode repetir o comando após perder a confirmação.
                            _LOGGER.warning(
                                "Falha ao confirmar %s de %s.", channel, self._device_id
                            )
        except aiomqtt.MqttError:
            self._record_mqtt_error()
            _LOGGER.warning("O canal de comandos de %s foi interrompido.", self._device_id)

    async def _send_heartbeats(self, client: aiomqtt.Client, presence: DevicePresence) -> None:
        try:
            while True:
                await asyncio.sleep(presence.heartbeat_interval_seconds)
                await presence.publish(client, "connected")
        except aiomqtt.MqttError:
            self._record_mqtt_error()
            _LOGGER.warning("Falha ao confirmar o heartbeat de %s.", self._device_id)

    async def _stop_presence_tasks(self) -> None:
        tasks, self._presence_tasks = self._presence_tasks, ()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _disconnect(self) -> None:
        try:
            await self._stop_presence_tasks()
        finally:
            self._client = None
            self._presence = None
            # Uma falha no encerramento de um socket perdido não deve impedir a reconexão.
            with suppress(aiomqtt.MqttError):
                await self._client_scope.aclose()


def _validate_telemetry_payload(payload: bytes) -> None:
    if not 0 < len(payload) <= 16 * 1024:
        raise ValueError("O payload de telemetria deve ter entre 1 byte e 16 KiB.")
