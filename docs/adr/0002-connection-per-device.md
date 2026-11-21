# ADR 0002: uma conexão MQTT por dispositivo

Status: aceito para a primeira implementação. Data: 2026-11-20.

## Contexto

O simulador pretende reproduzir identidade, falha e reconexão independentes de cada ESP32.

## Decisão

Criar um cliente e uma conexão MQTT por `device_id`, com `client_id` único e LWT próprio.

## Alternativa

Uma conexão multiplexada reduz custo de sockets, porém não reproduz presença e quedas individuais via LWT do broker.

## Consequências

O limite de dispositivos e o número de conexões simultâneas precisam ser medidos e documentados. O broker deve ter capacidade para a frota selecionada.
