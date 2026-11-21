"""Inicia a frota virtual com python -m esp32_fleet."""

import asyncio
import sys
from collections.abc import Sequence

import aiomqtt

from esp32_fleet.config.settings import parse_settings
from esp32_fleet.orchestration.fleet import run_fleet


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a CLI e devolve um código de saída para o terminal."""
    settings = parse_settings(argv)
    try:
        # O aiomqtt precisa de leitores de socket, inclusive no Windows.
        asyncio.run(run_fleet(settings), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"Configuração inválida: {exc}", file=sys.stderr)
        return 2
    except aiomqtt.MqttError as exc:
        print(f"Falha MQTT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
