"""Configura a execução de um dispositivo virtual no terminal."""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class SimulatorSettings:
    """Opções de conexão e cadência para uma execução."""

    broker: str
    port: int
    device_id: str
    interval: float
    seed: int | None
    samples: int | None


def parse_settings(argv: Sequence[str] | None = None) -> SimulatorSettings:
    """Interpreta os argumentos e verifica os limites aceitos pela CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m esp32_fleet",
        description="Publica sinais vitais sintéticos de um ESP32 virtual.",
    )
    parser.add_argument("--broker", default="127.0.0.1", help="Host do broker MQTT.")
    parser.add_argument("--port", type=int, default=1883, help="Porta TCP do broker.")
    parser.add_argument("--device-id", default="esp32-001", help="Identificador do dispositivo.")
    parser.add_argument("--interval", type=float, default=2.0, help="Intervalo em segundos.")
    parser.add_argument("--seed", type=int, help="Semente do gerador para reproduzir a série.")
    parser.add_argument("--samples", type=int, help="Quantidade de amostras antes de encerrar.")
    args = parser.parse_args(argv)

    broker = args.broker.strip()
    if not broker:
        parser.error("--broker deve informar o host do broker.")
    if not 1 <= args.port <= 65535:
        parser.error("--port deve estar entre 1 e 65535.")
    if not isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval deve ser positivo e finito.")
    if args.samples is not None and args.samples < 1:
        parser.error("--samples deve ser positivo.")

    return SimulatorSettings(
        broker, args.port, args.device_id, args.interval, args.seed, args.samples
    )
