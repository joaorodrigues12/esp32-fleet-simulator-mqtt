"""Verifica transições de sessão e decisões de idempotência por conexão."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from esp32_fleet.domain.models import SessionBinding, SessionState
from esp32_fleet.domain.sessions import (
    SessionAction,
    SessionCommand,
    SessionReason,
    SessionStateMachine,
)

_BOOT = UUID(int=100)
_NEXT_BOOT = UUID(int=101)
_SESSION_A = UUID(int=200)
_SESSION_B = UUID(int=201)
_NOW = datetime(2026, 11, 20, 18, tzinfo=UTC)


def _command(
    number: int,
    *,
    action: SessionAction = SessionAction.START,
    session_id: UUID = _SESSION_A,
    version: int = 1,
    boot_id: UUID = _BOOT,
) -> SessionCommand:
    return SessionCommand(
        command_id=UUID(int=number),
        boot_id=boot_id,
        action=action,
        session_id=session_id,
        session_version=version,
        occurred_at=_NOW - timedelta(seconds=5),
        expires_at=_NOW + timedelta(seconds=5),
    )


def test_start_end_start_preserves_previous_reading_binding() -> None:
    """Troca de sessão em ordem sem alterar o vínculo de uma leitura já gerada."""
    machine = SessionStateMachine(_BOOT)
    assert machine.binding.state is SessionState.IDLE
    assert machine.binding.session_version == 0

    start_a = _command(1, version=3)
    started = machine.apply(start_a, now=_NOW)
    sampled_binding = machine.binding
    assert started.command_id == start_a.command_id
    assert started.accepted and started.reason is SessionReason.APPLIED
    assert sampled_binding == SessionBinding(_SESSION_A, 3)
    assert sampled_binding.state is SessionState.ACTIVE

    ended = machine.apply(_command(2, action=SessionAction.END, version=4), now=_NOW)
    assert ended.accepted and ended.reason is SessionReason.APPLIED
    assert ended.binding == SessionBinding(None, 4)
    assert ended.binding.state is SessionState.IDLE

    started_b = machine.apply(_command(3, session_id=_SESSION_B, version=6), now=_NOW)
    assert started_b.accepted and started_b.reason is SessionReason.APPLIED
    assert machine.binding == SessionBinding(_SESSION_B, 6)
    assert sampled_binding == SessionBinding(_SESSION_A, 3)


def test_new_start_revision_refreshes_the_same_session() -> None:
    """Aceita uma revisão superior para uma sessão que continua ativa."""
    machine = SessionStateMachine(_BOOT)
    first = machine.apply(_command(1), now=_NOW)
    refreshed = machine.apply(_command(2, version=5), now=_NOW)

    assert refreshed.reason is SessionReason.APPLIED
    assert refreshed.accepted
    assert first.binding == SessionBinding(_SESSION_A, 1)
    assert refreshed.binding == SessionBinding(_SESSION_A, 5)


def test_applied_duplicate_reports_current_binding_even_after_expiration() -> None:
    """Responde à repetição de um comando aplicado sem restaurar a sessão antiga."""
    machine = SessionStateMachine(_BOOT)
    start = _command(1)
    machine.apply(start, now=_NOW)
    machine.apply(_command(2, action=SessionAction.END, version=2), now=_NOW)
    machine.apply(_command(3, session_id=_SESSION_B, version=3), now=_NOW)
    current_binding = machine.binding

    result = machine.apply(start, now=start.expires_at + timedelta(seconds=1))

    assert result.reason is SessionReason.DUPLICATE and result.accepted
    assert result.command_id == start.command_id
    assert result.binding is machine.binding is current_binding
    assert result.binding == SessionBinding(_SESSION_B, 3)


def test_rejected_duplicate_keeps_the_original_rejection() -> None:
    """Mantém a rejeição ao repetir um comando que seria aceitável no estado novo."""
    machine = SessionStateMachine(_BOOT)
    machine.apply(_command(1), now=_NOW)
    start_b = _command(2, session_id=_SESSION_B, version=4)
    assert machine.apply(start_b, now=_NOW).reason is SessionReason.WRONG_SESSION
    machine.apply(_command(3, action=SessionAction.END, version=3), now=_NOW)

    repeated = machine.apply(start_b, now=_NOW)
    conflict = machine.apply(replace(start_b, session_version=5), now=_NOW)

    assert repeated.reason is SessionReason.WRONG_SESSION and not repeated.accepted
    assert conflict.reason is SessionReason.CONFLICT and not conflict.accepted
    assert repeated.binding == conflict.binding == machine.binding == SessionBinding(None, 3)


def test_command_id_reuse_with_different_content_is_rejected() -> None:
    """Detecta conflito mesmo quando o conteúdo alterado tem revisão maior."""
    machine = SessionStateMachine(_BOOT)
    original = _command(1, version=2)
    machine.apply(original, now=_NOW)

    changed = replace(original, action=SessionAction.END, session_version=3)
    result = machine.apply(changed, now=_NOW)

    assert result.reason is SessionReason.CONFLICT and not result.accepted
    assert result.binding == machine.binding == SessionBinding(_SESSION_A, 2)
    assert machine.apply(original, now=_NOW).reason is SessionReason.DUPLICATE


@pytest.mark.parametrize("version", [0, 2])
def test_equal_or_lower_revision_is_stale(version: int) -> None:
    """Rejeita revisão igual ou anterior à última revisão aplicada."""
    machine = SessionStateMachine(_BOOT)
    machine.apply(_command(1, version=2), now=_NOW)

    result = machine.apply(_command(2, action=SessionAction.END, version=version), now=_NOW)

    assert result.reason is SessionReason.STALE_VERSION and not result.accepted
    assert result.binding == machine.binding == SessionBinding(_SESSION_A, 2)


def test_end_rejects_an_unrelated_or_inactive_session() -> None:
    """Permite encerrar apenas o identificador de sessão que está ativo."""
    machine = SessionStateMachine(_BOOT)
    idle = machine.apply(_command(1, action=SessionAction.END), now=_NOW)
    assert idle.reason is SessionReason.WRONG_SESSION
    assert idle.binding == SessionBinding()

    machine.apply(_command(2, version=2), now=_NOW)
    wrong = _command(3, action=SessionAction.END, version=3, session_id=_SESSION_B)
    result = machine.apply(wrong, now=_NOW)
    assert result.reason is SessionReason.WRONG_SESSION and not result.accepted
    assert result.binding == machine.binding == SessionBinding(_SESSION_A, 2)


def test_expiration_boundary_rejects_new_command_without_changing_state() -> None:
    """Trata o instante de expiração como o primeiro instante inválido."""
    machine = SessionStateMachine(_BOOT)
    command = _command(1)

    expired = machine.apply(command, now=command.expires_at)

    assert expired.reason is SessionReason.EXPIRED and not expired.accepted
    assert expired.binding == machine.binding == SessionBinding()
    valid = SessionStateMachine(_BOOT).apply(
        command, now=command.expires_at - timedelta(microseconds=1)
    )
    assert valid.reason is SessionReason.APPLIED


def test_new_boot_rejects_old_command_before_tracking_its_id() -> None:
    """Inicia em IDLE e impede uma mensagem antiga de afetar a conexão nova."""
    old = _command(1, version=3)
    previous_machine = SessionStateMachine(_BOOT)
    previous_machine.apply(old, now=_NOW)
    machine = SessionStateMachine(_NEXT_BOOT)

    invalid = machine.apply(old, now=_NOW)
    assert invalid.reason is SessionReason.INVALID_BOOT and not invalid.accepted
    assert invalid.binding == SessionBinding()
    assert previous_machine.binding == SessionBinding(_SESSION_A, 3)

    current = replace(old, boot_id=_NEXT_BOOT, session_id=_SESSION_B, session_version=6)
    accepted = machine.apply(current, now=_NOW)
    assert accepted.reason is SessionReason.APPLIED
    assert accepted.binding == SessionBinding(_SESSION_B, 6)


def test_rejects_invalid_temporal_inputs_and_negative_revision() -> None:
    """Protege o domínio contra revisões e instantes fora do contrato tipado."""
    command = _command(1)
    with pytest.raises(ValueError, match="revisão"):
        replace(command, session_version=-1)
    with pytest.raises(ValueError, match="UTC"):
        replace(command, occurred_at=_NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="posterior"):
        replace(command, expires_at=command.occurred_at)
    with pytest.raises(ValueError, match="UTC"):
        SessionStateMachine(_BOOT).apply(command, now=_NOW.replace(tzinfo=None))
