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
    """Publica amostras até atingir o limite configurado ou receber cancelamento."""
    handler = CommandHandler(settings.device_id)
    generator = VitalsGenerator(seed=settings.seed)
    last_boot: UUID | None = None
    sequence = count()

    def sample(boot_id: UUID) -> bytes:
        nonlocal last_boot, sequence
        if boot_id != last_boot:
            last_boot = boot_id
            sequence = count()
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
            "sequence_number": next(sequence),
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
        for published in count(1):
            await connection.publish_sample(sample)
            if published == settings.samples:
                break
            await asyncio.sleep(settings.interval)
