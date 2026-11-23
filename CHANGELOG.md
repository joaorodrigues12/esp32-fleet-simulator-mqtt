# Changelog

Todas as mudanças relevantes do projeto serão registradas neste arquivo.
O formato segue Keep a Changelog e Semantic Versioning.

## [Unreleased]

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
