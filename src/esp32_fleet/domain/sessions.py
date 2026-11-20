"""Aplica comandos de sessão ao vínculo local de uma conexão."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from esp32_fleet.domain.models import SessionBinding


class SessionAction(StrEnum):
    """Operações que alteram o vínculo de sessão."""

    START = "START"
    END = "END"


class SessionReason(StrEnum):
    """Motivos produzidos pelas regras do domínio para o ACK."""

    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE_VERSION = "STALE_VERSION"
    WRONG_SESSION = "WRONG_SESSION"
    EXPIRED = "EXPIRED"
    INVALID_BOOT = "INVALID_BOOT"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class SessionCommand:
    """Conteúdo de um comando de sessão após a validação do envelope MQTT."""

    command_id: UUID
    boot_id: UUID
    action: SessionAction
    session_id: UUID
    session_version: int
    occurred_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        """Valida a revisão do comando e sua janela de validade."""
        if self.session_version < 0:
            raise ValueError("A revisão de sessão deve ser não negativa.")
        if any(
            timestamp.utcoffset() != timedelta(0)
            for timestamp in (self.occurred_at, self.expires_at)
        ):
            raise ValueError("Os horários do comando devem estar em UTC.")
        if self.expires_at <= self.occurred_at:
            raise ValueError("A expiração deve ser posterior à criação do comando.")


@dataclass(frozen=True, slots=True)
class SessionDecision:
    """Resultado correlacionado e vínculo observado ao decidir um comando."""

    command_id: UUID
    reason: SessionReason
    binding: SessionBinding

    @property
    def accepted(self) -> bool:
        """Indica aplicação ou repetição de um comando já aplicado."""
        return self.reason in (SessionReason.APPLIED, SessionReason.DUPLICATE)


class SessionStateMachine:
    """Mantém a associação e o histórico de comandos de um único boot_id."""

    def __init__(self, boot_id: UUID) -> None:
        """Inicia uma conexão sem sessão, com revisão zero."""
        self._boot_id = boot_id
        self._binding = SessionBinding()
        self._seen: dict[UUID, tuple[SessionCommand, SessionReason]] = {}

    @property
    def binding(self) -> SessionBinding:
        """Retorna o vínculo imutável vigente para a próxima leitura."""
        return self._binding

    def apply(self, command: SessionCommand, *, now: datetime) -> SessionDecision:
        """Aplica um comando válido ou devolve o motivo da rejeição.

        Args:
            command: Comando enviado à conexão do dispositivo.
            now: Horário UTC em que o dispositivo avalia a expiração.

        Returns:
            Decisão com o vínculo vigente ao processar o comando.

        Raises:
            ValueError: Se o horário de avaliação não estiver em UTC.
        """
        if now.utcoffset() != timedelta(0):
            raise ValueError("O horário de avaliação deve estar em UTC.")
        if command.boot_id != self._boot_id:
            return SessionDecision(command.command_id, SessionReason.INVALID_BOOT, self._binding)

        previous = self._seen.get(command.command_id)
        if previous is not None:
            original, reason = previous
            if command != original:
                reason = SessionReason.CONFLICT
            elif reason is SessionReason.APPLIED:
                reason = SessionReason.DUPLICATE
            # Usa o vínculo vigente, pois a sessão pode ter avançado desde o comando original.
            return SessionDecision(command.command_id, reason, self._binding)

        reason = self._apply_new(command, now)
        self._seen[command.command_id] = (command, reason)
        return SessionDecision(command.command_id, reason, self._binding)

    def _apply_new(self, command: SessionCommand, now: datetime) -> SessionReason:
        if now >= command.expires_at:
            return SessionReason.EXPIRED
        if command.session_version <= self._binding.session_version:
            return SessionReason.STALE_VERSION

        next_session: UUID | None
        if command.action is SessionAction.START:
            if self._binding.session_id not in (None, command.session_id):
                return SessionReason.WRONG_SESSION
            next_session = command.session_id
        else:
            if self._binding.session_id != command.session_id:
                return SessionReason.WRONG_SESSION
            next_session = None

        self._binding = SessionBinding(next_session, command.session_version)
        return SessionReason.APPLIED
