"""Verifica o contrato da configuração da janela com a CLI existente."""

import shlex
from importlib import import_module
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def gui_window() -> ModuleType:
    """Carrega as funções da GUI quando o extra opcional está instalado."""
    pytest.importorskip("PySide6.QtWidgets")
    return import_module("esp32_fleet.gui.window")


def test_form_arguments_validate_with_the_existing_cli(gui_window: ModuleType) -> None:
    """Reusa a validação e normaliza o separador decimal digitado em PT-BR."""
    form = gui_window.FleetForm(
        device_id="frota",
        devices="3",
        scenario="temperature-rise",
        seed="-42",
        samples="4",
        interval="0,25",
        broker="localhost",
        port="28883",
    )
    settings = gui_window.validate_arguments(form.arguments())
    assert (settings.device_id, settings.devices, settings.scenario.value) == (
        "frota",
        3,
        "temperature-rise",
    )
    assert (settings.seed, settings.samples, settings.interval) == (-42, 4, 0.25)
    assert (settings.broker, settings.port, settings.metrics) == ("localhost", 28883, True)
    assert gui_window.preview_ids(settings, 0) == ["frota-001", "frota-002", "frota-003"]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"devices": "0"}, "--devices"),
        ({"interval": "nan"}, "--interval"),
        ({"port": "70000"}, "--port"),
        ({"device_id": "frota/1"}, "--device-id"),
    ],
)
def test_invalid_form_exposes_the_cli_validation_message(
    gui_window: ModuleType, changes: dict[str, str], field: str
) -> None:
    """Devolve um erro ao formulário antes de iniciar qualquer worker."""
    form = gui_window.FleetForm(**changes)
    with pytest.raises(ValueError, match=field):
        gui_window.validate_arguments(form.arguments())


def test_preview_paginates_large_fleets_and_preserves_single_device_default(
    gui_window: ModuleType,
) -> None:
    """Mantém a numeração da CLI calculando no máximo uma página por vez."""
    defaults = gui_window.validate_arguments(gui_window.FleetForm().arguments())
    assert gui_window.preview_ids(defaults, 0) == ["esp32-001"]
    assert defaults.samples is None and defaults.seed is None

    large = gui_window.validate_arguments(gui_window.FleetForm(devices="1001").arguments())
    page = gui_window.preview_ids(large, 0)
    assert len(page) == 100
    assert (page[0], page[-1]) == ("esp32-0001", "esp32-0100")
    assert gui_window.preview_ids(large, 10) == ["esp32-1001"]


def test_copied_command_quotes_values_for_both_shells(gui_window: ModuleType) -> None:
    """Mantém valores especiais como argumentos literais no comando copiado."""
    args = ["--broker=hostname';$(echo example)", "--seed=-42"]
    posix = gui_window.cli_command(args, powershell=False)
    powershell = gui_window.cli_command(args, powershell=True)
    assert shlex.split(posix)[6:] == args
    assert powershell.endswith("'--broker=hostname'';$(echo example)' '--seed=-42'")
