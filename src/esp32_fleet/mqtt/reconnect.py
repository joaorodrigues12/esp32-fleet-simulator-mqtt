"""Define o intervalo entre tentativas de publicação MQTT."""

from collections.abc import Iterator
from dataclasses import dataclass
from math import isfinite
from random import Random


@dataclass(frozen=True, slots=True)
class ConnectionPolicy:
    """Configura o número de novas tentativas e os limites do backoff, em segundos."""

    max_retries: int = 5
    initial_delay: float = 0.25
    max_delay: float = 4.0

    def __post_init__(self) -> None:
        """Valida a configuração de reconexão."""
        if self.max_retries < 0:
            raise ValueError("O número de novas tentativas não pode ser negativo.")
        if not (0 < self.initial_delay <= self.max_delay and isfinite(self.max_delay)):
            raise ValueError("Os atrasos devem ser positivos, finitos e em ordem crescente.")

    def retry_delays(self) -> Iterator[float]:
        """Produz um atraso para cada nova tentativa.

        Yields:
            Espera em segundos entre metade do limite corrente e o próprio limite.
        """
        random = Random()
        limit = self.initial_delay
        for _ in range(self.max_retries):
            yield random.uniform(limit / 2, limit)
            limit = min(limit * 2, self.max_delay)
