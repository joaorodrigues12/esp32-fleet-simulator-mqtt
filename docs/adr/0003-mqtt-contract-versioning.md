# ADR 0003: JSON, MQTT 3.1.1 e contratos versionados

Status: aceito para a primeira implementação. Data: 2026-11-20.

## Contexto

Os contratos devem ser legíveis, auditáveis e compatíveis com inspeção por ferramentas comuns, inclusive antes da integração com VetMonitor.

## Decisão

Utilizar MQTT 3.1.1, JSON validado com Pydantic, `schema_version` em todos os payloads e tópico por dispositivo. QoS 1 em todos os canais iniciais. Retain apenas em `status`; jamais em `command` ou `data`.

## Alternativas

MQTT 5 oferece expiração de mensagem nativa e propriedades adicionais; Protobuf reduz payloads. Ambos aumentam o escopo do protótipo e podem ser avaliados em versões futuras.

## Consequências

A aplicação implementa `expires_at`, `command_id`, `message_id` e controle de revisão, pois QoS 1 permite duplicatas e não assegura semântica de negócio exatamente uma vez. Mudanças incompatíveis exigem novo namespace ou migração explícita.
