# Changelog

Todas as mudanças relevantes do projeto serão registradas neste arquivo.
O formato segue Keep a Changelog e Semantic Versioning.

## [Unreleased]

### Added

- Tela desktop opcional para configurar, iniciar e parar ensaios de frota, com prévia de IDs e comando CLI copiável.

- Perfil autenticado do Mosquitto local e suporte a usuário e senha por variáveis de ambiente na CLI.
- Perfil TLS com validação da identidade do broker e permissões MQTT para as contas `simulator` e `controller`.
- Limite configurável de comandos em espera por dispositivo, com aviso quando a fila descarta novos comandos.
- Consumidor MQTT de referência com validação de telemetria v1, deduplicação em memória e saída JSON Lines.

## [0.1.0]

### Added

- Scaffold arquitetural, contratos MQTT e decisões iniciais.
- Configuração inicial de tipagem, lint e hooks de commit.
- Gerador de sinais vitais sintéticos com séries reproduzíveis por semente.
- Publicação MQTT de telemetria com reconexão e broker Mosquitto para desenvolvimento.
- Presença MQTT com status retido, heartbeat e Last Will, além do encaminhamento de comandos.
- Máquina de estados de sessão com revisão monotônica, idempotência e isolamento por conexão.
- Validação de comandos MQTT de sessão e publicação de ACKs e eventos correlacionados.
- CLI para publicar amostras de um dispositivo virtual com presença e vínculo de sessão.
- Orquestração de múltiplos dispositivos com conexões, sessões e séries independentes.
- Seleção de cenários sintéticos pela CLI, com perfil padrão e elevação reproduzível de temperatura.
- Simulação periódica de atrasos e omissões de telemetria, configurável pela CLI.
- Resumo JSON opcional por dispositivo com leituras confirmadas, omissões, falhas MQTT e reconexões.
- CI com Mosquitto no Ubuntu e no Windows e testes de retomada da frota após reiniciar o broker.
- Benchmark local com até 50 dispositivos, com coleta de entrega, latência, CPU, memória e tempo de recuperação.

### Validação de referência

- Em carga estável no Windows 11, o assinante recebeu 7.500 de 7.500 leituras de 50 dispositivos em 300,547 s. A CLI usou em média 0,738% de um processador lógico, com pico observado de 51,914 MiB de memória residente.
- Após o reinício do Mosquitto, os 50 dispositivos retomaram a publicação em até 1,049 s a partir do retorno da porta do broker. [Procedimento e resultados](docs/development.md#ensaio-de-recursos-e-tempo-de-recupera%C3%A7%C3%A3o)
