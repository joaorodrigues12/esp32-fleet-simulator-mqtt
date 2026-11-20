"""Valida comandos de sessão e prepara suas respostas MQTT."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from esp32_fleet.domain.models import SessionBinding
from esp32_fleet.domain.sessions import (
    SessionAction,
    SessionCommand,
    SessionReason,
    SessionStateMachine,
)

_LOGGER = logging.getLogger(__name__)
_MAX_PAYLOAD_BYTES = 16 * 1024


class SessionCommandV1(BaseModel):
    """Envelope MQTT aceito para comandos de sessão."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Annotated[int, Field(ge=1, le=1)]
    message_id: UUID
    command_id: UUID
    device_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
    boot_id: UUID
    occurred_at: AwareDatetime
    expires_at: AwareDatetime
    action: SessionAction
    session_id: UUID
    session_version: Annotated[int, Field(ge=0)]

    def to_domain(self) -> SessionCommand:
        """Converte o envelope validado em um comando com horários UTC."""
        return SessionCommand(
            command_id=self.command_id,
            boot_id=self.boot_id,
            action=self.action,
            session_id=self.session_id,
            session_version=self.session_version,
            occurred_at=self.occurred_at,
            expires_at=self.expires_at,
        )


@dataclass(frozen=True, slots=True)
class CommandReplies:
    """ACK correspondente ao comando e evento gerado quando a decisão altera a sessão."""

    ack: bytes
    event: bytes | None = None


class CommandHandler:
    """Processa comandos em sequência para um dispositivo e um boot de cada vez."""

    def __init__(self, device_id: str) -> None:
        """Vincula o handler ao identificador do dispositivo no tópico."""
        self._device_id = device_id
        self._boot_id: UUID | None = None
        self._machine: SessionStateMachine | None = None

    def binding_for_boot(self, boot_id: UUID) -> SessionBinding:
        """Obtém o vínculo imutável disponível no boot que está amostrando."""
        if self._boot_id == boot_id and self._machine is not None:
            return self._machine.binding
        return SessionBinding()

    def handle(
        self, payload: bytes, boot_id: UUID, *, now: datetime | None = None
    ) -> CommandReplies | None:
        """Interpreta o payload e devolve as respostas para a conexão que o recebeu.

        Args:
            payload: Corpo MQTT em JSON UTF-8.
            boot_id: Identificador da conexão receptora.
            now: Horário UTC opcional para avaliações reproduzíveis.

        Returns:
            ACK e eventual evento, ou None quando falta correlação segura.

        Raises:
            ValueError: Se o horário de avaliação não estiver em UTC.
        """
        if not 0 < len(payload) <= _MAX_PAYLOAD_BYTES:
            _LOGGER.warning("Tamanho de comando MQTT inválido para %s.", self._device_id)
            return None
        if self._machine is None or self._boot_id != boot_id:
            self._machine = SessionStateMachine(boot_id)
            self._boot_id = boot_id
        machine = self._machine
        now = now if now is not None else datetime.now(UTC)
        if now.utcoffset() != timedelta(0):
            raise ValueError("O horário de avaliação deve estar em UTC.")

        try:
            envelope = SessionCommandV1.model_validate_json(payload)
            if envelope.device_id != self._device_id:
                raise ValueError("O dispositivo do comando difere do tópico.")
            command = envelope.to_domain()
        except ValueError:
            command_id = _recover_command_id(payload)
            if command_id is None:
                _LOGGER.warning("Comando MQTT sem correlação válida para %s.", self._device_id)
                return None
            ack = self._ack(boot_id, now, machine.binding, command_id, False, "INVALID_PAYLOAD")
            return CommandReplies(ack)

        decision = machine.apply(command, now=now)
        ack = self._ack(
            boot_id, now, decision.binding, decision.command_id, decision.accepted, decision.reason
        )
        if decision.reason is not SessionReason.APPLIED:
            return CommandReplies(ack)

        event = self._envelope(boot_id, now, decision.binding)
        event["cause_command_id"] = str(decision.command_id)
        return CommandReplies(ack, json.dumps(event).encode("utf-8"))

    def _ack(
        self,
        boot_id: UUID,
        now: datetime,
        binding: SessionBinding,
        command_id: UUID,
        accepted: bool,
        reason: str,
    ) -> bytes:
        payload = self._envelope(boot_id, now, binding)
        payload.update(command_id=str(command_id), accepted=accepted, reason=reason)
        return json.dumps(payload).encode("utf-8")

    def _envelope(
        self, boot_id: UUID, now: datetime, binding: SessionBinding
    ) -> dict[str, str | int | None]:
        return {
            "schema_version": 1,
            "message_id": str(uuid4()),
            "device_id": self._device_id,
            "boot_id": str(boot_id),
            "occurred_at": now.isoformat(),
            "state": binding.state,
            "session_id": str(binding.session_id) if binding.session_id is not None else None,
            "session_version": binding.session_version,
        }


def _recover_command_id(payload: bytes) -> UUID | None:
    """Obtém a correlação de um JSON inválido para o contrato."""
    try:
        envelope = json.loads(payload.decode("utf-8"))
        if isinstance(envelope, dict) and isinstance(command_id := envelope.get("command_id"), str):
            return UUID(command_id)
    except (ValueError, RecursionError):
        pass
    return None
