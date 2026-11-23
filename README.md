# ESP32 Fleet Simulator — MQTT

Simulador de dispositivos ESP32 virtuais para testar consumidores MQTT com sinais sintéticos. A CLI executa um dispositivo ou uma frota, publica leituras e presença e responde a comandos de sessão.

## Início rápido

A versão `0.1.0` usa MQTT 3.1.1 e os contratos de payload v1. Os testes automatizados
rodam em Windows e Ubuntu.

### Pré-requisitos

- Python definido em `.python-version` (3.12.8).
- `uv` 0.13.9 para gerenciamento de ambiente e dependências, a mesma versão usada pelo CI.
- Docker com Compose para usar o broker fornecido pelo projeto ou um Mosquitto local em execução.

### Ver a primeira mensagem

Na raiz do repositório, instale as dependências, inicie o broker do projeto
e deixe o assinante aguardando uma mensagem.

```bash
uv python install
uv sync --locked --group dev
docker compose up -d mosquitto
docker compose exec -T mosquitto mosquitto_sub -t 'vitals/esp32-001/data' -q 1 -C 1
```

Em outro terminal, execute o comando da próxima seção. Se você já tem um Mosquitto
local, pode assinar o mesmo tópico com seu cliente MQTT e indicar o endereço do broker
com `--broker` e `--port`.

### Usar o perfil autenticado do broker

Na raiz do projeto, crie a conta `esp32` para o Mosquitto de desenvolvimento.
O utilitário solicitará a senha e o Compose manterá o arquivo de senhas em um
volume local. Use `-c` na primeira criação do arquivo. Nas alterações futuras,
execute o mesmo comando sem `-c` para preservar os usuários existentes.

```console
docker compose -f compose.yaml -f compose.auth.yaml run --rm mosquitto mosquitto_passwd -c /mosquitto/data/passwords esp32
docker compose -f compose.yaml -f compose.auth.yaml up -d mosquitto
```

Configure usuário e senha no terminal em que a CLI será executada. Em PowerShell:

```powershell
$env:ESP32_FLEET_MQTT_USERNAME = 'esp32'
$env:ESP32_FLEET_MQTT_PASSWORD = Read-Host 'Senha MQTT' -MaskInput
uv run --locked python -m esp32_fleet --samples 1 --metrics
```

Em Bash:

```bash
export ESP32_FLEET_MQTT_USERNAME=esp32
read -rsp 'Senha MQTT: ' ESP32_FLEET_MQTT_PASSWORD
export ESP32_FLEET_MQTT_PASSWORD
uv run --locked python -m esp32_fleet --samples 1 --metrics
```

Use a mesma senha criada no broker. O perfil publica a porta em `127.0.0.1`
e exige credenciais de cada cliente. Para confirmar a publicação, confira
`samples_acked: 1` no resumo da CLI. Depois de alterar a senha no volume,
reinicie o serviço para carregar o arquivo atualizado.

### Usar TLS e permissões por tópico

O perfil `compose.tls.yaml` publica a porta `8883` em `127.0.0.1`. Para um
ensaio local, gere certificados com a dependência de desenvolvimento `trustme`
em um diretório fora do repositório. O comando padrão emite um certificado
válido para `localhost` e `127.0.0.1`.

```console
mkdir "$HOME/mqtt-tls"
uv sync --locked --group dev
uv run --locked python -m trustme --dir "$HOME/mqtt-tls"
docker compose -f compose.tls.yaml run --rm -v "$HOME/mqtt-tls:/run/tls:ro" mosquitto sh -ec 'umask 077 && cp /run/tls/server.pem /mosquitto/data/server.pem && cp /run/tls/server.key /mosquitto/data/server.key && chmod 600 /mosquitto/data/server.key'
docker compose -f compose.tls.yaml run --rm mosquitto mosquitto_passwd -c /mosquitto/data/passwords simulator
docker compose -f compose.tls.yaml run --rm mosquitto mosquitto_passwd /mosquitto/data/passwords controller
docker compose -f compose.tls.yaml up -d mosquitto
```

Use `-c` apenas para a primeira criação do arquivo de senhas. Esse arquivo,
o certificado do servidor e a chave privada ficam em um volume dedicado.
No PowerShell, os comandos abaixo configuram a conta `simulator` e a CA
da CLI e publicam uma amostra.

```powershell
$env:ESP32_FLEET_MQTT_USERNAME = 'simulator'
$env:ESP32_FLEET_MQTT_PASSWORD = Read-Host 'Senha MQTT do simulador' -MaskInput
$env:ESP32_FLEET_MQTT_CA_FILE = "$HOME/mqtt-tls/client.pem"
uv run --locked python -m esp32_fleet --broker 127.0.0.1 --port 8883 --samples 1 --metrics
```

Em Bash, configure o usuário e a senha no ambiente e exporte
`ESP32_FLEET_MQTT_CA_FILE="$HOME/mqtt-tls/client.pem"` antes de executar a CLI.
O valor de `--broker` deve corresponder a uma identidade no certificado.

| Conta | Publica | Recebe |
| --- | --- | --- |
| `simulator` | `data`, `status`, `session` e `ack` | `command` |
| `controller` | `command` | `data`, `status`, `session` e `ack` |

As permissões valem para todos os IDs da frota. Use a conta `controller` em um
consumidor MQTT com a mesma CA. Em MQTT 3.1.1, verifique a entrega no assinante
para testar uma permissão de publicação.

## Estrutura

- `src/esp32_fleet/config` e `orchestration` configuram a CLI e executam a frota.
- `src/esp32_fleet/domain`, `handlers` e `mqtt` implementam sinais, sessões e comunicação.
- `src/esp32_fleet/observability` registra métricas por dispositivo.
- `tests` cobre unidades, contratos e integração com Mosquitto.

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

### Selecionar um cenário

A CLI usa `--scenario baseline` por padrão. Use `temperature-rise` para que a série
de temperatura parta da mesma referência de 36,7 °C e se aproxime gradualmente de
38,7 °C ao longo das amostras, com pequenas flutuações.

```bash
uv run --locked python -m esp32_fleet --scenario temperature-rise --seed 42 --samples 20
```

Com o mesmo cenário e a mesma semente, os valores dos sinais se repetem. Em modo
de frota, cada membro usa o cenário escolhido com sua própria semente derivada.
Os tópicos e os campos das mensagens MQTT permanecem os mesmos.

### Simular atraso e omissão de leituras

Use `--delay-every N --delay-seconds S` para esperar S segundos depois da coleta
de cada Nª leitura e antes da primeira tentativa de envio. O `sampled_at` indica
o instante da coleta. O intervalo normal entre ciclos é aplicado após o envio.

Use `--omit-every N` para pular o envio de cada Nª leitura. A série e a sequência
avançam, de modo que a próxima amostra publicada no mesmo `boot_id` revela a lacuna.
Quando as duas regras coincidem, a leitura é omitida sem aplicar o atraso.
`--samples` inclui os ciclos omitidos, e a contagem de ciclos é independente por dispositivo.

```bash
uv run --locked python -m esp32_fleet --seed 42 --samples 6 --omit-every 2 --delay-every 3 --delay-seconds 0.5
```

Em uma conexão contínua, o comando publica as sequências `0`, `2` e `4`, com atraso
na sequência `2`. Em uma reconexão, a sequência MQTT recomeça no novo `boot_id`
e a contagem dos ciclos do dispositivo continua.

### Consultar as métricas por dispositivo

Use `--metrics` para receber uma linha JSON em `stderr` quando cada dispositivo
encerrar, inclusive em caso de falha. As linhas de resumo têm o campo
`event` igual a `telemetry_metrics` e identificam o dispositivo em `device_id`.

```bash
uv run --locked python -m esp32_fleet --devices 3 --samples 6 --omit-every 2 --metrics
```

| Campo | O que conta |
| --- | --- |
| `samples_acked` | Leituras confirmadas pelo broker em QoS 1, uma vez por publicação lógica. |
| `samples_omitted` | Leituras omitidas pela regra `--omit-every`. |
| `mqtt_errors` | Falhas MQTT observadas em conexão, telemetria, presença ou comandos. Uma interrupção pode gerar várias falhas. |
| `reconnections` | Conexões bem-sucedidas após a primeira. |

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

### Limite da fila de comandos

Cada dispositivo aceita até 128 comandos em espera, além do comando em processamento.
Use `--command-queue-size N` para configurar a capacidade com um inteiro positivo.
Quando a fila lota, o cliente MQTT emite um aviso em `stderr` e descarta os novos
comandos até abrir espaço. Os comandos já enfileirados seguem na ordem de chegada.

O controlador confirma o processamento pelo ACK de aplicação em
`vitals/{device_id}/ack`, correlacionado pelo `command_id`. Se o ACK não chegar
no prazo, o controlador pode repetir o mesmo comando enquanto ele ainda for válido,
conforme as regras de recuperação. [Contratos MQTT](docs/contracts.md#recupera%C3%A7%C3%A3o-ap%C3%B3s-reconex%C3%A3o-ou-reinicializa%C3%A7%C3%A3o)

## Arquitetura e contratos

Leia [arquitetura](docs/architecture.md), [contratos](docs/contracts.md) e [ADRs](docs/adr/).

## Estratégia Git

A branch `main` recebe entregas revisadas em pull requests com o CI aprovado.
Consulte o [guia de desenvolvimento](docs/development.md) para reproduzir as verificações na sua máquina.

## Ensaios com a frota

A referência local com 50 dispositivos entregou 7.500 leituras em cerca de cinco minutos.
Em um ensaio separado, os 50 dispositivos retomaram a publicação após o reinício do broker.
Consulte o procedimento e os resultados de CPU, memória e recuperação no [guia de desenvolvimento](docs/development.md#ensaio-de-recursos-e-tempo-de-recupera%C3%A7%C3%A3o).

## Licença

MIT. Ver `LICENSE`.
