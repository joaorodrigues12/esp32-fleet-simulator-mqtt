"""Contabiliza a telemetria e a atividade MQTT de um dispositivo virtual."""

import json
from dataclasses import dataclass, field


@dataclass(slots=True)
class TelemetryMetrics:
    """Contadores locais mantidos durante a execução do dispositivo."""

    device_id: str
    samples_acked: int = 0
    samples_omitted: int = 0
    mqtt_errors: int = 0
    reconnections: int = 0
    _connected_once: bool = field(default=False, init=False)

    def record_connection(self) -> None:
        """Conta conexões bem-sucedidas após a primeira."""
        if self._connected_once:
            self.reconnections += 1
        self._connected_once = True

    def to_json(self) -> str:
        """Serializa os contadores públicos em uma linha para o terminal."""
        return json.dumps(
            {
                "event": "telemetry_metrics",
                "device_id": self.device_id,
                "samples_acked": self.samples_acked,
                "samples_omitted": self.samples_omitted,
                "mqtt_errors": self.mqtt_errors,
                "reconnections": self.reconnections,
            }
        )
