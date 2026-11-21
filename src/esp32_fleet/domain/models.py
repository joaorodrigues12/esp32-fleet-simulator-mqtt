"""Modelos imutáveis compartilhados pelo domínio do simulador."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class SessionState(StrEnum):
    """Estado local da associação de um dispositivo."""

    IDLE = "IDLE"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True)
class SessionBinding:
    """Identificador e revisão que uma leitura captura no instante da geração."""

    session_id: UUID | None = None
    session_version: int = 0

    @property
    def state(self) -> SessionState:
        """Deriva o estado da presença de um identificador de sessão."""
        return SessionState.IDLE if self.session_id is None else SessionState.ACTIVE
