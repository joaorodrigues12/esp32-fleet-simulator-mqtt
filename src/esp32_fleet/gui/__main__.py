"""Abre a interface desktop com python -m esp32_fleet.gui."""

import sys

from PySide6.QtWidgets import QApplication

from esp32_fleet.gui.window import FleetWindow


def main() -> int:
    """Inicia a janela e o loop gráfico."""
    app = QApplication(sys.argv)
    app.setApplicationName("ESP32 Fleet Simulator")
    app.setStyle("Fusion")
    window = FleetWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
