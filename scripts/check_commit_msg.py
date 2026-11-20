"""Valida mensagens de commit com padrão Conventional Commits."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r"^(feat|fix|docs|test|chore|refactor|ci|perf|style|build)(\([a-z0-9-]+\))?(!)?: .{3,72}$"
)


def main() -> int:
    """Verifica a primeira linha de uma mensagem de commit.

    Returns:
        Zero para mensagens válidas; um caso contrário.
    """
    if len(sys.argv) != 2:
        print("Usage: check_commit_msg.py <commit-message-path>", file=sys.stderr)
        return 1
    first_line = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[0]
    if PATTERN.fullmatch(first_line):
        return 0
    print("Commit message must follow Conventional Commits.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
