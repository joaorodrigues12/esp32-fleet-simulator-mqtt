"""Publica a telemetria de um dispositivo virtual em MQTT 3.1.1."""

import asyncio
import re
from contextlib import AsyncExitStack, suppress
from math import isfinite
from types import TracebackType
from typing import Self

import aiomqtt

from esp32_fleet.mqtt.reconnect import ConnectionPolicy

_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class MqttConnection:
    """Reutiliza uma conexão MQTT por dispositivo dentro de um contexto assíncrono."""

    def __init__(
        self,
        device_id: str,
        *,
        hostname: str = "127.0.0.1",
        port: int = 1883,
        policy: ConnectionPolicy | None = None,
        timeout: float = 3.0,
    ) -> None:
        """Configura a publicação de telemetria para um dispositivo.

        Args:
            device_id: Identificador operacional usado no tópico e no cliente MQTT.
            hostname: Host do broker.
            port: Porta TCP do broker.
            policy: Política de reconexão usada em cada publicação.
            timeout: Tempo em segundos para aguardar confirmações do protocolo.

        Raises:
            ValueError: Se a identificação, a porta ou o timeout forem inválidos.
        """
        if not _DEVICE_ID.fullmatch(device_id):
            raise ValueError("Identificador de dispositivo MQTT inválido.")
        if not 1 <= port <= 65535:
            raise ValueError("A porta MQTT deve estar entre 1 e 65535.")
        if not (isfinite(timeout) and timeout > 0):
            raise ValueError("O timeout MQTT deve ser positivo e finito.")
        self._device_id = device_id
        self._topic = f"vitals/{device_id}/data"
        self._hostname = hostname
        self._port = port
        self._policy = policy if policy is not None else ConnectionPolicy()
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._client_scope = AsyncExitStack()
        self._client: aiomqtt.Client | None = None
        self._active = False

    async def __aenter__(self) -> Self:
        """Abre o escopo de uso. A primeira publicação inicia a conexão."""
        if self._active:
            raise RuntimeError("A conexão MQTT já está em uso.")
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
        if not 0 < len(payload) <= 16 * 1024:
            raise ValueError("O payload de telemetria deve ter entre 1 byte e 16 KiB.")
        async with self._lock:
            if not self._active:
                raise RuntimeError("Abra o contexto MQTT antes de publicar.")
            delays = self._policy.retry_delays()
            while True:
                try:
                    if self._client is None:
                        self._client = await self._client_scope.enter_async_context(
                            aiomqtt.Client(
                                self._hostname,
                                self._port,
                                identifier=f"esp32-fleet-{self._device_id}",
                                protocol=aiomqtt.ProtocolVersion.V311,
                                clean_session=True,
                                timeout=self._timeout,
                            )
                        )
                    await self._client.publish(self._topic, payload, qos=1, retain=False)
                    return
                except aiomqtt.MqttError:
                    await self._disconnect()
                    delay = next(delays, None)
                    if delay is None:
                        raise
                except asyncio.CancelledError:
                    await self._disconnect()
                    raise
                await asyncio.sleep(delay)

    async def _disconnect(self) -> None:
        self._client = None
        # Uma falha no encerramento de um socket perdido não deve impedir a reconexão.
        with suppress(aiomqtt.MqttError):
            await self._client_scope.aclose()
