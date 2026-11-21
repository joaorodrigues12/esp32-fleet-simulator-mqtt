"""Coordena o início e o encerramento dos dispositivos virtuais."""

import asyncio
import logging
from dataclasses import replace

import aiomqtt

from esp32_fleet.config.settings import SimulatorSettings
from esp32_fleet.orchestration.application import run_device

_LOGGER = logging.getLogger(__name__)


async def run_fleet(settings: SimulatorSettings) -> None:
    """Executa cada membro com conexão e estado próprios.

    Args:
        settings: Configuração do broker, dos dispositivos e das amostras.

    Raises:
        aiomqtt.MqttError: Se algum membro esgotar as tentativas de comunicação.
    """
    if settings.devices == 1:
        await run_device(settings)
        return

    async with asyncio.TaskGroup() as fleet:
        workers = []
        for index in range(settings.devices):
            number = index + 1
            device_id = f"{settings.device_id}-{number:0{settings.device_number_width}d}"
            # Uma semente por membro preserva a reprodução sem repetir a mesma série.
            seed = abs(settings.seed) + index if settings.seed is not None else None
            member = replace(settings, device_id=device_id, seed=seed, devices=1)
            workers.append(fleet.create_task(_run_member(member), name=device_id))

    failed = sum(not worker.result() for worker in workers)
    if failed:
        raise aiomqtt.MqttError(f"{failed} de {settings.devices} dispositivos falharam.")


async def _run_member(settings: SimulatorSettings) -> bool:
    try:
        await run_device(settings)
    except aiomqtt.MqttError as exc:
        _LOGGER.error("Falha MQTT em %s: %s", settings.device_id, exc)
        return False
    return True
