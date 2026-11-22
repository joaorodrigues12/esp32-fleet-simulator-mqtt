# ESP32 Fleet Simulator — MQTT

Simulador independente de dispositivos ESP32 virtuais que publicará sinais fisiológicos sintéticos via MQTT. O projeto servirá para testes de carga, falhas e contratos de integração, inclusive com o VetMonitor no futuro, sem incorporar seu backend ou dados de pacientes.

## Estado atual

O projeto executa uma frota de ESP32 virtuais pela CLI, gera sinais vitais sintéticos, publica telemetria e presença MQTT e responde a comandos de sessão.

## Pré-requisitos planejados

- Python definido em `.python-version` (3.12.8).
- `uv` 0.13.9 para gerenciamento de ambiente e dependências, a mesma versão usada pelo CI.
- Docker e Docker Compose para executar o broker de desenvolvimento.
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
uv python install
uv sync --locked --group dev
uv run --locked pre-commit install --hook-type pre-commit --hook-type commit-msg
```

O `uv` lê a versão do Python de `.python-version` e instala as dependências registradas em `uv.lock`.

## Executar um dispositivo virtual

Com o broker local em execução, este comando publica três amostras e encerra:

```bash
uv run --locked python -m esp32_fleet --device-id esp32-001 --seed 42 --samples 3
```

O broker padrão é `127.0.0.1:1883`. Use `--broker` e `--port` para indicar outro endereço,
`--interval` para definir o intervalo em segundos e `--help` para ver todas as opções.
Para executar continuamente, omita `--samples` e pressione `Ctrl+C` para encerrar.
Cada amostra guarda o `boot_id` e o vínculo de sessão vigentes no instante da geração.

## Executar uma frota

```bash
uv run --locked python -m esp32_fleet --devices 3 --device-id esp32 --seed 42 --samples 4
```

O comando inicia `esp32-001`, `esp32-002` e `esp32-003` e publica quatro amostras por dispositivo.
Cada membro mantém sua própria conexão, sequência, sessão e gerador. Com uma semente explícita,
a frota usa seu valor absoluto como base e soma a posição do dispositivo, começando em zero.

Em modo de frota, `--device-id` é o prefixo dos IDs e seu valor padrão é `esp32`. O modo de
um dispositivo continua usando `esp32-001` como ID padrão. `Ctrl+C` encerra toda a frota.
Quando um membro esgota suas tentativas de comunicação, os demais continuam. Se a frota chegar
ao fim após uma falha, a CLI termina com código 1.

## Gerar sinais vitais sintéticos

Use uma instância de `VitalsGenerator` por série. Cada chamada avança a série e retorna uma amostra
com frequência cardíaca em bpm, frequência respiratória em respirações por minuto e temperatura em
graus Celsius. Informe a mesma semente para reproduzir a série em testes.

```python
from esp32_fleet.domain.vitals import VitalsGenerator

generator = VitalsGenerator(seed=42)
sample = generator.generate()
print(sample)
```

## Publicar uma amostra no Mosquitto

Execute `docker compose up -d mosquitto`. O broker de desenvolvimento aceita conexões anônimas
na porta 1883 do loopback. Em outro terminal, rode o comando abaixo para receber uma mensagem:

```bash
docker compose exec -T mosquitto mosquitto_sub -t 'vitals/esp32-001/data' -q 1 -C 1
```

Execute este exemplo no ambiente Python do projeto:

```python
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from aiomqtt import Message

from esp32_fleet.domain.vitals import VitalsGenerator
from esp32_fleet.mqtt.connection import MqttConnection


def on_command(message: Message) -> None:
    print(f"Comando recebido em {message.topic}")


async def main() -> None:
    device_id = "esp32-001"
    async with MqttConnection(device_id, on_command=on_command) as connection:
        boot_id = connection.boot_id
        assert boot_id is not None
        sample = VitalsGenerator(seed=42).generate()
        now = datetime.now(UTC).isoformat()
        payload = {
            "schema_version": 1,
            "message_id": str(uuid4()),
            "device_id": device_id,
            "boot_id": str(boot_id),
            "occurred_at": now,
            "sampled_at": now,
            "sequence_number": 0,
            "session_id": None,
            "session_version": 0,
            **asdict(sample),
        }
        await connection.publish_telemetry(json.dumps(payload).encode("utf-8"))


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
```

O exemplo usa `SelectorEventLoop` para compatibilidade com o `aiomqtt` no Windows. Em caso de falha,
a próxima publicação pode restabelecer a conexão. As tentativas da mesma publicação reutilizam
os bytes da mensagem, inclusive o `message_id`, para deduplicação pelo consumidor.

## Acompanhar presença

Forneça `on_command` para habilitar presença. O dispositivo anuncia `online` após o SUBACK e
renova o status a cada `heartbeat_interval_seconds`. O callback síncrono recebe a mensagem
MQTT para encaminhamento rápido. Use `on_session_command` para processar comandos e publicar as respostas.
Em modo exclusivo de telemetria, a conexão abre na primeira publicação.

O status usa QoS 1 e fica retido em `vitals/{device_id}/status`. O encerramento normal confirma
`offline` antes de desconectar. Em uma queda abrupta do dispositivo, o broker publica o LWT
retido com o mesmo `boot_id`. Esse identificador muda ao abrir outra conexão.

Para testar reconexão e presença, disponibilize `mosquitto` no PATH ou defina
`MOSQUITTO_EXECUTABLE` com o caminho absoluto do executável. Rode
`uv run --locked pytest -q tests/integration/test_mqtt_connection_integration.py`.
Cada teste inicia seu próprio broker em uma porta local livre.

## Validação do estado de sessão

`SessionStateMachine` mantém o vínculo em memória por `boot_id`. A instância começa em `IDLE`
e devolve a decisão e o vínculo vigente para cada comando `START` ou `END`. Uma leitura pode
guardar esse vínculo imutável para preservar a associação mesmo depois de trocar de sessão.

Rode `uv run --locked pytest -q tests/unit/test_sessions.py` para verificar a troca de sessão,
revisões, expiração, duplicatas e comandos de uma conexão anterior.

## Processar comandos de sessão no Mosquitto

Com o broker em execução, passe `CommandHandler.handle` como callback da conexão. O callback
valida o JSON em `vitals/{device_id}/command` e aplica a decisão ao `boot_id` receptor.

```python
import asyncio

from esp32_fleet.handlers.commands import CommandHandler
from esp32_fleet.mqtt.connection import MqttConnection


async def main() -> None:
    device_id = "esp32-001"
    handler = CommandHandler(device_id)
    async with MqttConnection(device_id, on_session_command=handler.handle):
        await asyncio.Event().wait()


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
```

Envie `START` e `END` com o `boot_id` anunciado em `status`. A conexão publica o ACK em
`vitals/{device_id}/ack` e os eventos aplicados em `vitals/{device_id}/session`, com QoS 1 e sem
retenção. Um comando inválido com `command_id` recuperável recebe `INVALID_PAYLOAD`. Use
`uv run --locked pytest -q tests/integration/test_mqtt_sessions.py` para exercitar o fluxo.

## Arquitetura e contratos

Leia [arquitetura](docs/architecture.md), [contratos](docs/contracts.md) e [ADRs](docs/adr/).

## Estratégia Git

A branch `main` recebe entregas revisadas em pull requests com o CI aprovado.
Consulte o [guia de desenvolvimento](docs/development.md) para reproduzir as verificações na sua máquina.

## Próximas funcionalidades

1. Cenários de falha e testes de carga.

## Licença

MIT. Ver `LICENSE`.
