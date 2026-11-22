"""Verifica o limite do ensaio e a coleta de uma frota pequena com Mosquitto."""

import json
import os
import subprocess
import sys
from pathlib import Path

from test_mqtt_connection_integration import mosquitto_executable as mosquitto_executable

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_fleet.py"


def test_benchmark_reports_each_sample_from_a_small_fleet(mosquitto_executable: str) -> None:
    """Confirma a contagem e os indicadores de entrega do relatório."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--devices", "3", "--samples", "4", "--interval", "0.1"],
        env={**os.environ, "MOSQUITTO_EXECUTABLE": mosquitto_executable},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["requested_devices"] == report["observed_devices"] == 3
    assert report["samples_per_device"] == 4
    assert report["expected_samples"] == report["unique_samples"] == 12
    assert report["sequence_gaps"] == report["duplicates"] == report["sequence_collisions"] == 0
    assert report["reconnected_devices"] == 0
    assert report["elapsed_seconds"] > 0 and report["messages_per_second"] > 0
    assert report["latency_p95_ms"] >= report["latency_p50_ms"]
    assert report["complete"] is True


def test_benchmark_rejects_more_than_fifty_devices() -> None:
    """Mantém o teto da medição mesmo quando o broker está disponível."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--devices", "51"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--devices deve estar entre 1 e 50" in result.stderr
