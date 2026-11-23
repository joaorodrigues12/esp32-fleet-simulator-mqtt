"""Testes de reprodução, variação e limites da série de sinais vitais."""

from itertools import pairwise

import pytest

from esp32_fleet.domain.vitals import VitalsGenerator, VitalSigns, VitalsScenario


@pytest.mark.parametrize("scenario", list(VitalsScenario))
def test_same_seed_reproduces_the_series(scenario: VitalsScenario) -> None:
    """Verifica que a mesma semente reproduz cada perfil."""
    first = VitalsGenerator(seed=42, scenario=scenario)
    second = VitalsGenerator(seed=42, scenario=scenario)

    first_series = [first.generate() for _ in range(100)]
    second_series = [second.generate() for _ in range(100)]

    assert first_series == second_series


def test_default_and_explicit_baseline_keep_the_original_series() -> None:
    """Preserva as primeiras amostras conhecidas antes da opção de cenário."""
    expected = [
        VitalSigns(82, 17, 36.67),
        VitalSigns(84, 18, 36.64),
        VitalSigns(82, 17, 36.65),
        VitalSigns(80, 17, 36.66),
    ]
    default = VitalsGenerator(seed=42)
    baseline = VitalsGenerator(seed=42, scenario=VitalsScenario.BASELINE)
    assert [default.generate() for _ in expected] == expected
    assert [baseline.generate() for _ in expected] == expected


def test_temperature_rise_preserves_other_signals_and_stays_bounded() -> None:
    """Verifica a tendência, a variação por amostra e os limites do contrato."""
    rising = VitalsGenerator(seed=42, scenario=VitalsScenario.TEMPERATURE_RISE)
    baseline = VitalsGenerator(seed=42)
    temperatures = []
    for _ in range(100):
        elevated = rising.generate()
        normal = baseline.generate()
        assert elevated.heart_rate_bpm == normal.heart_rate_bpm
        assert elevated.respiratory_rate_rpm == normal.respiratory_rate_rpm
        assert elevated.temperature_c > normal.temperature_c
        assert 30.0 <= elevated.temperature_c <= 43.0
        temperatures.append(elevated.temperature_c)

    assert temperatures[0] < temperatures[9] < temperatures[19]
    assert 38.5 <= temperatures[-1] <= 38.9
    assert all(abs(current - previous) <= 0.35 for previous, current in pairwise(temperatures))


def test_different_seeds_produce_distinct_series() -> None:
    """Verifica que duas sementes podem gerar trajetórias diferentes."""
    first = VitalsGenerator(seed=42)
    second = VitalsGenerator(seed=43)

    assert [first.generate() for _ in range(20)] != [second.generate() for _ in range(20)]


def test_signals_vary_gradually_and_stay_within_the_contract() -> None:
    """Verifica os limites do contrato e a variação entre amostras consecutivas."""
    generator = VitalsGenerator(seed=42)
    series = [generator.generate() for _ in range(1_000)]

    assert len({sample.heart_rate_bpm for sample in series}) > 1
    assert len({sample.respiratory_rate_rpm for sample in series}) > 1
    assert len({sample.temperature_c for sample in series}) > 1

    for sample in series:
        assert type(sample.heart_rate_bpm) is int
        assert type(sample.respiratory_rate_rpm) is int
        assert 20 <= sample.heart_rate_bpm <= 250
        assert 4 <= sample.respiratory_rate_rpm <= 80
        assert 30.0 <= sample.temperature_c <= 43.0

    for previous, current in pairwise(series):
        assert abs(current.heart_rate_bpm - previous.heart_rate_bpm) <= 7
        assert abs(current.respiratory_rate_rpm - previous.respiratory_rate_rpm) <= 3
        assert abs(current.temperature_c - previous.temperature_c) <= 0.12
