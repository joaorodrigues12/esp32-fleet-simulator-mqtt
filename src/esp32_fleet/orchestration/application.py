"""Combina geração, sessões e publicação de um dispositivo virtual."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import count
from uuid import UUID, uuid4

from esp32_fleet.config.settings import SimulatorSettings
from esp32_fleet.domain.vitals import VitalsGenerator
from esp32_fleet.handlers.commands import CommandHandler
from esp32_fleet.mqtt.connection import MqttConnection


async def run_device(settings: SimulatorSettings) -> None:
    """Executa os ciclos de amostragem até atingir o limite ou receber cancelamento."""
    handler = CommandHandler(settings.device_id)
    generator = VitalsGenerator(seed=settings.seed, scenario=settings.scenario)
    last_boot: UUID | None = None
    sequence = count()

    def next_sequence(boot_id: UUID | None) -> int:
        nonlocal last_boot, sequence
        if boot_id != last_boot:
            last_boot = boot_id
            sequence = count()
        return next(sequence)

    def sample(boot_id: UUID) -> bytes:
        # A leitura guarda o vínculo vigente neste passo, mesmo se o envio for repetido.
        binding = handler.binding_for_boot(boot_id)
        timestamp = datetime.now(UTC).isoformat()
        payload = {
            "schema_version": 1,
            "message_id": str(uuid4()),
            "device_id": settings.device_id,
            "boot_id": str(boot_id),
            "occurred_at": timestamp,
            "sampled_at": timestamp,
            "sequence_number": next_sequence(boot_id),
            "session_id": str(binding.session_id) if binding.session_id is not None else None,
            "session_version": binding.session_version,
            **asdict(generator.generate()),
        }
        return json.dumps(payload).encode("utf-8")

    async with MqttConnection(
        settings.device_id,
        hostname=settings.broker,
        port=settings.port,
        on_session_command=handler.handle,
    ) as connection:
        for cycle in count(1):
            if settings.omit_every is not None and cycle % settings.omit_every == 0:
                # A série avança mesmo se o consumidor não receber a leitura do ciclo.
                next_sequence(connection.boot_id)
                generator.generate()
            else:
                delay = 0.0
                if settings.delay_every is not None and cycle % settings.delay_every == 0:
                    delay = settings.delay_seconds
                await connection.publish_sample(sample, delay_seconds=delay)
            if cycle == settings.samples:
                break
            await asyncio.sleep(settings.interval)
