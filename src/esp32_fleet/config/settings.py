"""Configura a execução de dispositivos virtuais no terminal."""

import argparse
import re
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class SimulatorSettings:
    """Opções de conexão, cadência e tamanho da frota."""

    broker: str
    port: int
    device_id: str
    interval: float
    seed: int | None
    samples: int | None
    devices: int = 1

    @property
    def device_number_width(self) -> int:
        """Largura do número usado nos identificadores da frota."""
        return max(3, len(str(self.devices)))


def parse_settings(argv: Sequence[str] | None = None) -> SimulatorSettings:
    """Interpreta os argumentos e verifica os limites aceitos pela CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m esp32_fleet",
        description="Publica sinais vitais sintéticos de uma frota de ESP32 virtuais.",
    )
    parser.add_argument("--broker", default="127.0.0.1", help="Host do broker MQTT.")
    parser.add_argument("--port", type=int, default=1883, help="Porta TCP do broker.")
    parser.add_argument("--device-id", help="Identificador único ou prefixo dos IDs da frota.")
    parser.add_argument("--devices", type=int, default=1, help="Quantidade de dispositivos.")
    parser.add_argument("--interval", type=float, default=2.0, help="Intervalo em segundos.")
    parser.add_argument("--seed", type=int, help="Semente do gerador para reproduzir a série.")
    parser.add_argument("--samples", type=int, help="Quantidade de amostras por dispositivo.")
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
    if args.devices < 1:
        parser.error("--devices deve ser positivo.")

    device_id = args.device_id
    if device_id is None:
        device_id = "esp32-001" if args.devices == 1 else "esp32"
    settings = SimulatorSettings(
        broker, args.port, device_id, args.interval, args.seed, args.samples, args.devices
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", device_id):
        parser.error("--device-id deve seguir o formato de identificador MQTT do projeto.")
    if settings.devices > 1 and len(device_id) + 1 + settings.device_number_width > 64:
        parser.error("--device-id é longo demais para acrescentar o número do dispositivo.")
    return settings
