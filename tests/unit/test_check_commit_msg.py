"""Testes para o validador de mensagens de commit."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("message", "expected_returncode", "expected_stderr"),
    [
        ("docs: revisar a descrição do simulador", 0, ""),
        ("revisar a descrição do simulador", 1, "Commit message must follow Conventional Commits."),
    ],
    ids=["mensagem-valida", "mensagem-invalida"],
)
def test_commit_message_hook(
    tmp_path: Path, message: str, expected_returncode: int, expected_stderr: str
) -> None:
    """Verifica o código de saída e o diagnóstico que o hook entrega ao Git."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_commit_msg.py"
    message_file = tmp_path / "commit-msg"
    message_file.write_text(f"{message}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), str(message_file)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stderr
    assert result.stderr.strip() == expected_stderr
