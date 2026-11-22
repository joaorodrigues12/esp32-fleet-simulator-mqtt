"""Mede telemetria da CLI contra uma instância local e isolada do Mosquitto."""

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import UUID

import aiomqtt

_MAX_DEVICES = 50
_DEVICE_PREFIX = "bench"
_HOST = "127.0.0.1"


class Measurements:
    """Conta mensagens, revisões de conexão e sequências da frota esperada."""

    def __init__(self, device_ids: set[str], samples_per_device: int) -> None:
        self._sequences: dict[str, set[int]] = {device_id: set() for device_id in device_ids}
        self._boots: dict[str, set[UUID]] = {device_id: set() for device_id in device_ids}
        self._message_ids: set[UUID] = set()
        self._latencies_ms: list[float] = []
        self._samples_per_device = samples_per_device
        self._duplicates = 0
        self._collisions = 0

    def record(self, message: aiomqtt.Message, received_at: datetime) -> None:
        """Valida a origem e registra uma leitura recebida pelo assinante."""
        sample = json.loads(message.payload)
        device_id = sample["device_id"]
        sequence = sample["sequence_number"]
        if (
            device_id not in self._sequences
            or str(message.topic) != f"vitals/{device_id}/data"
            or type(sequence) is not int
            or not 0 <= sequence < self._samples_per_device
        ):
            raise ValueError("Origem ou sequência fora do cenário de carga.")

        message_id = UUID(sample["message_id"])
        if message_id in self._message_ids:
            self._duplicates += 1
            return

        self._message_ids.add(message_id)
        self._boots[device_id].add(UUID(sample["boot_id"]))
        sequences = self._sequences[device_id]
        if sequence in sequences:
            self._collisions += 1
        sequences.add(sequence)
        sampled_at = datetime.fromisoformat(sample["sampled_at"])
        self._latencies_ms.append((received_at - sampled_at).total_seconds() * 1_000)

    def report(self, elapsed: float) -> dict[str, int | float | bool | None]:
        """Resume a entrega e a latência das amostras únicas."""
        expected = len(self._sequences) * self._samples_per_device
        missing = expected - sum(map(len, self._sequences.values()))
        reconnected = sum(len(boots) > 1 for boots in self._boots.values())
        latencies = sorted(self._latencies_ms)
        return {
            "requested_devices": len(self._sequences),
            "observed_devices": sum(bool(sequences) for sequences in self._sequences.values()),
            "samples_per_device": self._samples_per_device,
            "expected_samples": expected,
            "unique_samples": len(self._message_ids),
            "sequence_gaps": missing,
            "duplicates": self._duplicates,
            "sequence_collisions": self._collisions,
            "reconnected_devices": reconnected,
            "elapsed_seconds": round(elapsed, 3),
            "messages_per_second": round(len(self._message_ids) / elapsed, 3),
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "complete": missing == self._collisions == reconnected == 0,
        }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return round(values[ceil(len(values) * fraction) - 1], 3)


@contextmanager
def _process(args: list[str], log_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with (
        log_path.open("wb") as log,
        subprocess.Popen(
            args, stdout=log, stderr=subprocess.STDOUT, creationflags=flags
        ) as process,
    ):
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


async def _wait_for_broker(process: subprocess.Popen[bytes], port: int, log: Path) -> None:
    try:
        async with asyncio.timeout(5):
            while process.poll() is None:
                try:
                    _, writer = await asyncio.open_connection(_HOST, port)
                except OSError:
                    await asyncio.sleep(0.025)
                    continue
                writer.close()
                await writer.wait_closed()
                return
    except TimeoutError:
        pass
    raise RuntimeError(
        f"Mosquitto indisponível.\n{log.read_text(encoding='utf-8', errors='replace')}"
    )


async def _collect(observer: aiomqtt.Client, measurements: Measurements) -> None:
    async for message in observer.messages:
        measurements.record(message, datetime.now(UTC))


async def _measure(
    executable: str, devices: int, samples: int, interval: float
) -> dict[str, int | float | bool | None]:
    device_ids = (
        {_DEVICE_PREFIX}
        if devices == 1
        else {f"{_DEVICE_PREFIX}-{index:03}" for index in range(1, devices + 1)}
    )
    measurements = Measurements(device_ids, samples)
    with socket.socket() as reservation:
        reservation.bind((_HOST, 0))
        port = reservation.getsockname()[1]
    with TemporaryDirectory(prefix="esp32-benchmark-") as directory:
        workdir = Path(directory)
        config, broker_log = workdir / "mosquitto.conf", workdir / "mosquitto.log"
        config.write_text(
            f"listener {port} {_HOST}\nallow_anonymous true\npersistence false\nlog_dest stdout\n",
            encoding="utf-8",
        )
        args = [
            sys.executable,
            "-m",
            "esp32_fleet",
            "--port",
            str(port),
            "--device-id",
            _DEVICE_PREFIX,
            "--devices",
            str(devices),
            "--samples",
            str(samples),
            "--interval",
            str(interval),
            "--seed",
            "42",
        ]
        fleet_log = workdir / "fleet.log"
        with _process([executable, "-c", str(config)], broker_log) as broker:
            await _wait_for_broker(broker, port, broker_log)
            async with aiomqtt.Client(
                _HOST, port, timeout=3, protocol=aiomqtt.ProtocolVersion.V311
            ) as observer:
                await observer.subscribe("vitals/+/data", qos=1)
                started = perf_counter()
                with _process(args, fleet_log) as fleet:
                    async with asyncio.TaskGroup() as readers:
                        reader = readers.create_task(_collect(observer, measurements))
                        exit_code = await asyncio.to_thread(
                            fleet.wait, timeout=samples * interval + 30
                        )
                        elapsed = perf_counter() - started
                        # Dá tempo ao assinante para drenar mensagens já encaminhadas pelo broker.
                        await asyncio.sleep(0.5)
                        reader.cancel()
                if exit_code:
                    log = fleet_log.read_text(encoding="utf-8", errors="replace")
                    raise RuntimeError(f"A CLI encerrou com código {exit_code}.\n{log}")
    return measurements.report(elapsed)


def main(argv: Sequence[str] | None = None) -> int:
    """Executa o ensaio e imprime o relatório JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=_MAX_DEVICES)
    parser.add_argument("--samples", type=int, default=150)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not 1 <= args.devices <= _MAX_DEVICES:
        parser.error(f"--devices deve estar entre 1 e {_MAX_DEVICES}.")
    if args.samples < 1 or not isfinite(args.interval) or args.interval <= 0:
        parser.error(
            "Informe uma quantidade de amostras positiva e um intervalo finito e positivo."
        )
    executable = os.environ.get("MOSQUITTO_EXECUTABLE") or shutil.which("mosquitto")
    if executable is None:
        parser.error("Disponibilize mosquitto no PATH ou defina MOSQUITTO_EXECUTABLE.")
    try:
        report = asyncio.run(
            _measure(executable, args.devices, args.samples, args.interval),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, aiomqtt.MqttError, ExceptionGroup) as exc:
        print(f"Falha no ensaio: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
