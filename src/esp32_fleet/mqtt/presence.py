"""Monta as mensagens DeviceStatusV1 de uma conexão MQTT."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import aiomqtt

type PresenceReason = Literal["connected", "graceful_shutdown", "unexpected_disconnect"]


@dataclass(frozen=True, slots=True)
class DevicePresence:
    """Identidade e intervalo dos anúncios de presença de uma conexão."""

    device_id: str
    boot_id: UUID
    heartbeat_interval_seconds: int

    @property
    def topic(self) -> str:
        """Retorna o tópico onde o broker retém o último status."""
        return f"vitals/{self.device_id}/status"

    def will(self) -> aiomqtt.Will:
        """Prepara o anúncio que o broker envia em caso de perda abrupta."""
        return aiomqtt.Will(self.topic, self._encode("unexpected_disconnect"), qos=1, retain=True)

    async def publish(self, client: aiomqtt.Client, reason: PresenceReason) -> None:
        """Publica e aguarda a confirmação do status retido com QoS 1."""
        await client.publish(self.topic, self._encode(reason), qos=1, retain=True)

    def _encode(self, reason: PresenceReason) -> bytes:
        payload = {
            "schema_version": 1,
            "message_id": str(uuid4()),
            "device_id": self.device_id,
            "boot_id": str(self.boot_id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "state": "online" if reason == "connected" else "offline",
            "reason": reason,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
        }
        return json.dumps(payload).encode("utf-8")
