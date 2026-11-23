"""Mede entrega, recursos e recuperação da CLI com Mosquitto local e isolado."""

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import ExitStack, asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import UUID

import aiomqtt
import psutil  # type: ignore[import-untyped]

_MAX_DEVICES = 50
_DEVICE_PREFIX = "bench"
_HOST = "127.0.0.1"
_RECOVERY_TIMEOUT = 12

type _Report = dict[str, int | float | bool | None | dict[str, float]]
type _BootReadings = dict[str, tuple[UUID, float]]


@dataclass(frozen=True, slots=True)
class _Run:
    """Parâmetros e arquivos temporários usados pelos dois modos do ensaio."""

    broker_args: list[str]
    fleet_args: list[str]
    directory: Path
    device_ids: set[str]
    port: int
    fleet_timeout: float

    @property
    def fleet_log(self) -> Path:
        return self.directory / "fleet.log"


class _Resources:
    """Amostra CPU acumulada e memória residente da árvore do simulador."""

    def __init__(self) -> None:
        self._cpu_by_pid: dict[int, float] = {}
        self._peak_rss_bytes = 0
        self.samples = 0

    async def collect(self, pid: int) -> None:
        """Amostra a cada 250 ms enquanto o processo existir."""
        with suppress(psutil.NoSuchProcess):
            root = psutil.Process(pid)
            while True:
                # No Windows, o Python da venv pode iniciar o interpretador como filho.
                rss = 0
                for process in (root, *root.children(recursive=True)):
                    with suppress(psutil.NoSuchProcess), process.oneshot():
                        cpu = process.cpu_times()
                        rss += process.memory_info().rss
                        self._cpu_by_pid[process.pid] = cpu.user + cpu.system
                if rss:
                    self.samples += 1
                    self._peak_rss_bytes = max(self._peak_rss_bytes, rss)
                await asyncio.sleep(0.25)

    def report(self, elapsed: float) -> _Report:
        """Usa o tempo de parede da CLI e o último tempo de CPU visto de cada PID."""
        cpu_seconds = sum(self._cpu_by_pid.values())
        return {
            "resource_samples": self.samples,
            "fleet_cpu_seconds_sampled": round(cpu_seconds, 3),
            "fleet_cpu_average_percent": round(cpu_seconds / elapsed * 100, 3),
            "fleet_peak_rss_mib": round(self._peak_rss_bytes / (1024**2), 3),
        }


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

    def report(self, elapsed: float) -> _Report:
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


@asynccontextmanager
async def _data_observer(port: int) -> AsyncIterator[aiomqtt.Client]:
    async with aiomqtt.Client(
        _HOST, port, timeout=3, protocol=aiomqtt.ProtocolVersion.V311
    ) as observer:
        await observer.subscribe("vitals/+/data", qos=1)
        yield observer


async def _wait_for_fleet(fleet: subprocess.Popen[bytes], run: _Run) -> None:
    try:
        exit_code = await asyncio.to_thread(fleet.wait, timeout=run.fleet_timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("A CLI não encerrou no prazo do ensaio.") from exc
    if exit_code:
        log = run.fleet_log.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"A CLI encerrou com código {exit_code}.\n{log}")


async def _measure_load(run: _Run, samples: int) -> _Report:
    measurements = Measurements(run.device_ids, samples)
    resources = _Resources()
    broker_log = run.directory / "mosquitto.log"
    with _process(run.broker_args, broker_log) as broker:
        await _wait_for_broker(broker, run.port, broker_log)
        async with _data_observer(run.port) as observer:
            started = perf_counter()
            with _process(run.fleet_args, run.fleet_log) as fleet:
                async with asyncio.TaskGroup() as readers:
                    reader = readers.create_task(_collect(observer, measurements))
                    sampler = readers.create_task(resources.collect(fleet.pid))
                    await _wait_for_fleet(fleet, run)
                    elapsed = perf_counter() - started
                    sampler.cancel()
                    # Dá tempo ao assinante para drenar mensagens já encaminhadas pelo broker.
                    await asyncio.sleep(0.5)
                    reader.cancel()
    report = measurements.report(elapsed)
    report.update(resources.report(elapsed))
    report["complete"] = bool(report["complete"]) and resources.samples > 0
    return report


async def _record_boot_readings(
    observer: aiomqtt.Client,
    device_ids: set[str],
    observed: _BootReadings,
    previous: _BootReadings,
) -> None:
    """Guarda a primeira leitura observada de cada dispositivo na nova conexão."""
    while observed.keys() != device_ids:
        message = await anext(observer.messages)
        received_at = perf_counter()
        sample = json.loads(message.payload)
        device_id = sample["device_id"]
        if device_id not in device_ids or str(message.topic) != f"vitals/{device_id}/data":
            raise ValueError("A leitura não pertence à frota do ensaio.")
        boot = UUID(sample["boot_id"])
        if device_id in previous and boot == previous[device_id][0]:
            continue  # Um reenvio de leitura anterior não demonstra retomada.
        observed.setdefault(device_id, (boot, received_at))


async def _measure_recovery(run: _Run) -> _Report:
    initial: _BootReadings = {}
    recovered: _BootReadings = {}
    broker_log = run.directory / "mosquitto.log"
    with ExitStack() as fleet_scope:
        with _process(run.broker_args, broker_log) as broker:
            await _wait_for_broker(broker, run.port, broker_log)
            async with _data_observer(run.port) as observer:
                fleet = fleet_scope.enter_context(_process(run.fleet_args, run.fleet_log))
                try:
                    async with asyncio.timeout(_RECOVERY_TIMEOUT):
                        await _record_boot_readings(observer, run.device_ids, initial, {})
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"Apenas {len(initial)} de {len(run.device_ids)} dispositivos "
                        "publicaram antes da interrupção."
                    ) from exc
            broker.kill()
            await asyncio.to_thread(broker.wait, timeout=5)

        await asyncio.sleep(0.5)
        if fleet.poll() is not None:
            raise RuntimeError("A CLI encerrou antes do retorno do broker.")

        restart_log = run.directory / "mosquitto-restart.log"
        with _process(run.broker_args, restart_log) as broker:
            await _wait_for_broker(broker, run.port, restart_log)
            ready_at = perf_counter()
            async with _data_observer(run.port) as observer:
                with suppress(TimeoutError):
                    async with asyncio.timeout(_RECOVERY_TIMEOUT):
                        await _record_boot_readings(observer, run.device_ids, recovered, initial)
            await _wait_for_fleet(fleet, run)

    latencies = {
        device_id: (received_at - ready_at) * 1_000
        for device_id, (_, received_at) in sorted(recovered.items())
    }
    values = sorted(latencies.values())
    return {
        "requested_devices": len(run.device_ids),
        "observed_before_restart": len(initial),
        "recovered_devices": len(recovered),
        "recovery_p50_ms": _percentile(values, 0.50),
        "recovery_p95_ms": _percentile(values, 0.95),
        "recovery_max_ms": round(values[-1], 3) if values else None,
        "recovery_by_device_ms": {key: round(value, 3) for key, value in latencies.items()},
        "complete": recovered.keys() == run.device_ids,
    }


async def _measure(
    executable: str,
    devices: int,
    samples: int,
    interval: float,
    *,
    restart_broker: bool,
) -> _Report:
    device_ids = (
        {_DEVICE_PREFIX}
        if devices == 1
        else {f"{_DEVICE_PREFIX}-{index:03}" for index in range(1, devices + 1)}
    )
    with socket.socket() as reservation:
        reservation.bind((_HOST, 0))
        port = reservation.getsockname()[1]
    with TemporaryDirectory(prefix="esp32-benchmark-") as directory:
        workdir = Path(directory)
        config = workdir / "mosquitto.conf"
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
        run = _Run(
            broker_args=[executable, "-c", str(config)],
            fleet_args=args,
            directory=workdir,
            device_ids=device_ids,
            port=port,
            fleet_timeout=samples * interval + 30,
        )
        if restart_broker:
            return await _measure_recovery(run)
        return await _measure_load(run, samples)


def main(argv: Sequence[str] | None = None) -> int:
    """Executa o ensaio e imprime o relatório JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=_MAX_DEVICES)
    parser.add_argument("--samples", type=int, default=150)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--restart-broker", action="store_true", help="Mede a retomada após reiniciar o Mosquitto."
    )
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
            _measure(
                executable,
                args.devices,
                args.samples,
                args.interval,
                restart_broker=args.restart_broker,
            ),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, aiomqtt.MqttError, ExceptionGroup, psutil.Error) as exc:
        print(f"Falha no ensaio: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
