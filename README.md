# ESP32 Fleet Simulator — MQTT

Simulador independente de dispositivos ESP32 virtuais que publicará sinais fisiológicos sintéticos via MQTT. O projeto servirá para testes de carga, falhas e contratos de integração, inclusive com o VetMonitor no futuro, sem incorporar seu backend ou dados de pacientes.

## Estado atual

**Scaffold arquitetural.** Não há CLI funcional, clientes MQTT nem gerador de dados nesta entrega.

## Pré-requisitos planejados

- Python definido em `.python-version` (3.12.8).
- `uv` para gerenciamento de ambiente e dependências.
- Docker e Docker Compose para o broker Mosquitto (a configuração será implementada em branch posterior).
- Git.

## Estrutura

- `src/esp32_fleet/config`: configurações.
- `src/esp32_fleet/mqtt`: transporte, roteamento e tópicos.
- `src/esp32_fleet/handlers`: contratos de entrada e saída.
- `src/esp32_fleet/domain`: dispositivos, sessões, sinais vitais e cenários.
- `src/esp32_fleet/orchestration`: ciclo de vida da frota.
- `src/esp32_fleet/observability`: métricas e logs.
- `src/esp32_fleet/ports`: interfaces para testes.
- `tests`: testes unitários, de contrato e integração.
- `docs/architecture.md`: arquitetura, classes e concorrência.
- `docs/contracts.md`: tópicos, payloads e regras MQTT.
- `docs/adr`: decisões arquiteturais.

## Preparação local

```bash
uv python install 3.12.8
uv sync --group dev
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg
uv run pre-commit run --all-files
```

A resolução das dependências exige acesso ao índice de pacotes. `uv.lock` deve ser mantido no repositório após execução bem-sucedida de `uv lock`.

## Arquitetura e contratos

Leia [arquitetura](docs/architecture.md), [contratos](docs/contracts.md) e [ADRs](docs/adr/).

## Estratégia Git

Branch principal estável e branches curtas `feature/*` ou `fix/*`. Cada entrega passa por testes, revisão e Pull Request. Este pacote contém histórico Git **local**; não comprova PR nem CI remoto.

## Próximas funcionalidades

1. Configuração e CLI.
2. Gerador fisiológico com testes.
3. Transporte MQTT com reconexão e LWT.
4. Sessões e confirmação de comandos.
5. Orquestração de múltiplos dispositivos.
6. Cenários de falha, carga e CI.

## Licença

MIT. Ver `LICENSE`.
