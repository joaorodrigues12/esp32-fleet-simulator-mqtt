"""Gera séries sintéticas de sinais vitais para o simulador."""

from dataclasses import dataclass
from random import Random

_HEART_RATE_REFERENCE = 82.0
_RESPIRATORY_RATE_REFERENCE = 17.0
_TEMPERATURE_REFERENCE = 36.7
_RETURN_TO_REFERENCE = 0.15


@dataclass(frozen=True, slots=True)
class VitalSigns:
    """Valores sintéticos de uma amostra, nas unidades do contrato MQTT."""

    heart_rate_bpm: int
    respiratory_rate_rpm: int
    temperature_c: float


class VitalsGenerator:
    """Mantém uma série de sinais que pode ser reproduzida com uma semente."""

    def __init__(self, seed: int | None = None) -> None:
        """Inicializa o estado e a fonte de aleatoriedade da série.

        Args:
            seed: Semente da instância. Use o mesmo inteiro para reproduzir a série.
        """
        self._random = Random(seed)
        self._heart_rate = _HEART_RATE_REFERENCE
        self._respiratory_rate = _RESPIRATORY_RATE_REFERENCE
        self._temperature = _TEMPERATURE_REFERENCE

    def generate(self) -> VitalSigns:
        """Avança a série em um passo e produz a próxima amostra.

        Returns:
            Frequência cardíaca e respiratória inteiras e temperatura em graus Celsius.
        """
        # O estímulo compartilhado faz as frequências tenderem a variar juntas.
        activity = self._random.uniform(-1.0, 1.0)
        self._heart_rate = _advance(
            self._heart_rate,
            reference=_HEART_RATE_REFERENCE,
            variation=2.5 * activity + self._random.uniform(-0.5, 0.5),
            minimum=20,
            maximum=250,
        )
        self._respiratory_rate = _advance(
            self._respiratory_rate,
            reference=_RESPIRATORY_RATE_REFERENCE,
            variation=0.8 * activity + self._random.uniform(-0.3, 0.3),
            minimum=4,
            maximum=80,
        )
        self._temperature = _advance(
            self._temperature,
            reference=_TEMPERATURE_REFERENCE,
            variation=self._random.uniform(-0.05, 0.05),
            minimum=30.0,
            maximum=43.0,
        )
        return VitalSigns(
            heart_rate_bpm=round(self._heart_rate),
            respiratory_rate_rpm=round(self._respiratory_rate),
            temperature_c=round(self._temperature, 2),
        )


def _advance(
    current: float, *, reference: float, variation: float, minimum: float, maximum: float
) -> float:
    """Aplica a variação com retorno gradual à referência e limita o resultado."""
    updated = current + (reference - current) * _RETURN_TO_REFERENCE + variation
    return min(maximum, max(minimum, updated))
