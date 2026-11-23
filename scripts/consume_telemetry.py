"""Consome leituras MQTT v1 e emite telemetria válida em JSON Lines."""

import argparse
import asyncio
import sys
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

import aiomqtt
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from esp32_fleet.config.settings import MqttSettings, add_mqtt_arguments, parse_mqtt_options

_TOPIC_FILTER = "vitals/+/data"
_DEFAULT_DEDUP_WINDOW = 4096
_MAX_PAYLOAD_BYTES = 16 * 1024


class VitalsDataV1(BaseModel):
    """Representa os campos e as faixas do contrato de leitura MQTT v1."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Annotated[int, Field(ge=1, le=1)]
    message_id: UUID
    device_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
    boot_id: UUID
    occurred_at: AwareDatetime
    sampled_at: AwareDatetime
    sequence_number: Annotated[int, Field(ge=0)]
    session_id: UUID | None
    session_version: Annotated[int, Field(ge=0)]
    heart_rate_bpm: Annotated[int, Field(ge=20, le=250)]
    respiratory_rate_rpm: Annotated[int, Field(ge=4, le=80)]
    temperature_c: Annotated[float, Field(ge=30, le=43, allow_inf_nan=False)]

    @field_validator("occurred_at", "sampled_at")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        """Exige horário UTC no envelope de telemetria."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("O horário da leitura deve usar UTC.")
        return value


def _decode(message: aiomqtt.Message) -> VitalsDataV1:
    payload = message.payload
    if not isinstance(payload, bytes) or not 0 < len(payload) <= _MAX_PAYLOAD_BYTES:
        raise ValueError("Tamanho ou formato inválido do payload.")
    if message.qos != 1 or message.retain:
        raise ValueError("A leitura deve usar QoS 1 e chegar sem retenção.")
    reading = VitalsDataV1.model_validate_json(payload)
    if str(message.topic) != f"vitals/{reading.device_id}/data":
        raise ValueError("O tópico diverge do dispositivo da leitura.")
    return reading


async def _consume(settings: MqttSettings, limit: int | None, dedup_window: int) -> None:
    seen: OrderedDict[UUID, None] = OrderedDict()
    accepted = 0
    async with aiomqtt.Client(
        settings.broker,
        settings.port,
        username=settings.username,
        password=settings.password,
        tls_context=settings.tls_context,
        protocol=aiomqtt.ProtocolVersion.V311,
    ) as client:
        if tuple(await client.subscribe(_TOPIC_FILTER, qos=1)) != (1,):
            raise aiomqtt.MqttError("O broker não confirmou a assinatura de telemetria com QoS 1.")
        print(f"Assinatura ativa em {_TOPIC_FILTER}.", file=sys.stderr, flush=True)
        async for message in client.messages:
            try:
                reading = _decode(message)
            except ValueError:
                print("Leitura descartada pelo contrato VitalsDataV1.", file=sys.stderr, flush=True)
                continue

            if reading.message_id in seen:
                continue
            seen[reading.message_id] = None
            if len(seen) > dedup_window:
                seen.popitem(last=False)

            print(reading.model_dump_json(), flush=True)
            accepted += 1
            if accepted == limit:
                return


def main(argv: Sequence[str] | None = None) -> int:
    """Executa o consumidor e informa falhas MQTT pelo código de saída."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_mqtt_arguments(parser)
    parser.add_argument(
        "--messages", type=int, metavar="N", help="Encerra após N leituras aceitas."
    )
    parser.add_argument(
        "--dedup-window",
        type=int,
        metavar="N",
        default=_DEFAULT_DEDUP_WINDOW,
        help=f"Quantidade de IDs lembrados. Padrão: {_DEFAULT_DEDUP_WINDOW}.",
    )
    args = parser.parse_args(argv)
    if args.messages is not None and args.messages < 1:
        parser.error("--messages deve ser um inteiro positivo.")
    if args.dedup_window < 1:
        parser.error("--dedup-window deve ser um inteiro positivo.")
    settings = parse_mqtt_options(parser, args)

    try:
        asyncio.run(
            _consume(settings, args.messages, args.dedup_window),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except KeyboardInterrupt:
        return 130
    except aiomqtt.MqttError as exc:
        print(f"Falha MQTT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
