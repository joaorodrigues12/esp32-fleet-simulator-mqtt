"""Executa a engine em um processo filho controlado pela entrada padrão."""

import asyncio
import os
import sys
from collections.abc import Sequence
from contextlib import suppress
from threading import Thread

import aiomqtt

from esp32_fleet.config.settings import SimulatorSettings, parse_settings
from esp32_fleet.orchestration.fleet import run_fleet


async def _run(settings: SimulatorSettings) -> None:
    loop = asyncio.get_running_loop()
    fleet = asyncio.create_task(run_fleet(settings))

    def stop_when_input_closes() -> None:
        # A leitura sem buffer pode ficar bloqueada até o fim natural do processo.
        # Ela não bloqueia o event loop nem mantém um lock dos streams do Python.
        with suppress(OSError):
            os.read(sys.stdin.fileno(), 1)
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(fleet.cancel)

    Thread(target=stop_when_input_closes, name="controle-do-ensaio", daemon=True).start()
    with suppress(asyncio.CancelledError):
        await fleet


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a configuração da CLI até a conclusão ou a solicitação de parada."""
    settings = parse_settings(argv)
    try:
        asyncio.run(_run(settings), loop_factory=asyncio.SelectorEventLoop)
    except aiomqtt.MqttError as exc:
        print(f"Falha MQTT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
