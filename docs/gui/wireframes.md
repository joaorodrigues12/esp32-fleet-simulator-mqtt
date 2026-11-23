# Interface de controle da frota

Wireframes da aplicação desktop em PySide6/Qt Widgets. Os números e identificadores
nas telas são exemplos. A área principal preserva a seleção quando recebe atualizações.

## Navegação e estados comuns

```text
ESP32 Fleet Simulator                       Broker 127.0.0.1:1883 · Conectado
────────────────────────────────────────────────────────────────────────────
Frota                     Área da tela selecionada
Configuração
Métricas em tempo real
Conexões e integrações
Histórico e comparação
Configurações globais
────────────────────────────────────────────────────────────────────────────
Engine v0.1.0 · main · revisão do ensaio                     Abra o log de eventos
```

- O cabeçalho mostra o broker selecionado, o uso de TLS e a última verificação.
  A referência de CI aparece em Configurações globais com link e revisão.
- Uma ação em andamento mostra o alvo e o estado. O resultado aparece na linha
  do dispositivo ou do ensaio e no log da execução.
- Um erro de conexão identifica a operação e sugere conferir o endereço, as
  credenciais ou a CA conforme o motivo. Os campos preenchidos ficam disponíveis
  para correção. A mensagem distingue erro de conexão, rejeição pelo dispositivo
  e espera por ACK.
- A navegação e a tabela funcionam por teclado. Estados têm rótulo textual,
  foco visível e descrição acessível. Números e horários são apresentados em
  PT-BR e a exportação mantém unidades e formatos de máquina.

## 1. Frota

```text
Frota
Perfil [frota-cardiaca-50 ▼]    Quantidade [50] [Adicionar dispositivos]
[Executar ensaio reproduzível] [Parar ensaio] [Copiar comando CLI]
Buscar [esp32-]    Conectividade [Todos ▼]    Sessão [Todas ▼]

Seleção  Dispositivo  Conexão  Sessão  Boot   Confirmadas  Omissões  Falhas  Retomadas  Última leitura
[ ]      esp32-001    ONLINE   ACTIVE  a04b…  127          0        0       1          14:51:24
[x]      esp32-002    ONLINE   IDLE    103b…  129          2        0       0          14:51:25
[ ]      esp32-003    OFFLINE  —       c782…  118          0        1       0          14:51:03

Seleção · 1 dispositivo
[Iniciar] [Pausar / Retomar] [Parar] [Reconectar] [Forçar queda]
[Abrir sessão] [Encerrar sessão] [Ver detalhes]
Detalhes    boot_id completo · session_id · última revisão · eventos do dispositivo
```

A quantidade recebe qualquer inteiro positivo compatível com os IDs gerados.
Acima do limiar de aviso, a tela informa o possível impacto de CPU, memória,
sockets e capacidade do broker. A tabela mantém conectividade e sessão como
campos distintos. A pausa é um estado da execução local e aparece junto à ação
do dispositivo. Uma seleção em lote dá um resultado por dispositivo.

| Situação | Resposta da tela |
| --- | --- |
| Frota vazia | Preencher quantidade e selecionar **Adicionar dispositivos**. |
| Aguardando primeiro anúncio | Mostrar **Aguardando conexão** e manter o boot atual indisponível até o primeiro `status`. |
| Ensaio em execução | Exibir os contadores confirmados e a origem de cada leitura. Ações exibem o resultado por dispositivo. |
| Ensaio encerrado ou falhou | Manter o último resultado observado e indicar quais dispositivos encerraram ou falharam. |

## 2. Configuração

```text
Configuração                                      Aplicar a [Perfil / Dispositivo ▼]
Identidade    Prefixo [esp32]  Quantidade [50]  IDs previstos [esp32-001 … esp32-050]
Sinais        Cenário [baseline ▼]  Semente [42] [Gerar nova]  [Parâmetros do gerador]
Execução      Intervalo (s) [2,0]  Amostras [150]  Duração total [    ]
Perturbações  Atrasar a cada [  ] ciclos por [   ] ms  Omitir a cada [  ] ciclos
              Falha MQTT simulada [    ]
Comandos      Fila em espera [128]  Política [Avisar e descartar novos]
Conexão       Host [127.0.0.1]  Porta [1883]  Keepalive [   ]  Clean session [ ]
              Modelo de client_id [                   ]
Segurança     Conta [simulator ▼]  [Selecionar credencial]  TLS [ ]
              CA [Selecionar]  Certificado e chave de cliente [Selecionar]
              Identidade do broker [Verificar]  [Ver permissões do perfil]
Sessão        Referência de sessão [   ]  Referência externa sintética [   ]
              [Salvar preset] [Duplicar] [Importar JSON] [Exportar JSON]
                                                             [Aplicar à frota]
```

As opções disponíveis mostram o valor padrão e o equivalente de terminal. Na
primeira versão da tela, campos que dependem de suporte da engine exibem
**Planejado** com a dependência. O valor de atraso em milissegundos é convertido
em segundos para o comando existente.

| Situação | Resposta da tela |
| --- | --- |
| Carregando preset | Preservar o formulário atual até validar o conteúdo importado. |
| Campo inválido | Explicar o erro junto ao campo e mover o foco para o primeiro erro ao tentar salvar. |
| Ensaio ativo | Indicar qual configuração foi congelada nessa execução. As edições ficam disponíveis para o próximo ensaio. |
| Cofre indisponível | Exibir a falha do cofre e manter a senha apenas durante a operação em que ela foi digitada. |

## 3. Métricas em tempo real

```text
Métricas em tempo real            Ensaio [Ensaio atual ▼]  Dispositivo [Todos ▼]
Leituras/s   Omissões (%)   Falhas MQTT   Reconexões   Latência ACK   Fila em espera

[ Série de leituras/s e omissões ]        [ ACK p50/p95 e ocupação por dispositivo ]
[ Frequência cardíaca ] [ Frequência respiratória ] [ Temperatura ]

Telemetria        [Sinais ▼]  [Ver JSON]  [Exibir lacunas]  [Exibir duplicatas]
Horário de coleta   Dispositivo   Sequência   FC     FR    Temperatura   Sessão

Log de eventos    [Todos ▼]
Horário   Dispositivo   Evento                                  Detalhes
14:51:22  esp32-002     Sessão aberta                            ACK correlacionado

Referência local · 50 dispositivos                        [Ver método do benchmark]
```

Os gráficos indicam a unidade, a janela e a fonte da métrica. Uma tabela fornece
os mesmos valores por dispositivo. Os eventos de presença, ACK, reconexão,
descarte e Last Will usam horário observado e preservam o contexto do boot.
A referência de 50 dispositivos exibe o resultado registrado no benchmark.

| Situação | Resposta da tela |
| --- | --- |
| Coleta iniciada | Mostrar **Aguardando primeiras leituras**. |
| Métrica ainda indisponível | Mostrar **Sem dados** com a fonte esperada. |
| Conexão interrompida | Preservar a última amostra com seu horário e indicar que está desatualizada. |
| Ensaio encerrado | Manter os gráficos e o log consultáveis pelo histórico, quando o armazenamento estiver ativo. |

## 4. Conexões e integrações

```text
Conexões e integrações
Broker      Host [127.0.0.1]   Porta [8883]   TLS [Ativo]   [Testar conectividade]
            Perfil [controller ▼]  Última verificação [   ]  Resultado [   ]

Contrato MQTT v1                  [Copiar contrato] [Payloads JSON]
Canal          Tópico                          QoS  Retenção  Exemplo
Telemetria     vitals/{device_id}/data          1    Não       [Ver]
Presença       vitals/{device_id}/status        1    Sim       [Ver]
Comandos       vitals/{device_id}/command       1    Não       [Ver]
ACK            vitals/{device_id}/ack           1    Não       [Ver]
Sessão         vitals/{device_id}/session       1    Não       [Ver]

Consumidor externo   [Python / paho-mqtt ▼] [Copiar exemplo]
VetMonitor           Ambiente [   ]  Conta controller [Selecionar credencial]
                     Estado [Pendente] [Ver evidência]

Consumidor embutido  [Iniciar] [Parar]  Leituras aceitas [0] [Exportar leituras]
                     [ Fluxo JSON por leitura aceita ]
```

Os exemplos Python e Node.js assinam `vitals/+/data` e seguem o contrato v1
com deduplicação por `message_id`. O teste do broker mostra as etapas executadas,
incluindo conexão e assinatura quando aplicável. O estado **Ativa** para o VetMonitor
exige evidência de troca de mensagens no ambiente de homologação.

| Situação | Resposta da tela |
| --- | --- |
| Teste em andamento | Mostrar a etapa atual e o tempo de espera. |
| Configuração registrada | Mostrar **Configurada** quando os dados de homologação estiverem preenchidos. |
| Teste falhou | Mostrar a etapa e o motivo conhecidos. Manter a última evidência bem-sucedida datada. |
| Consumidor em execução | Atualizar o contador e disponibilizar CSV, JSON ou Parquet conforme a capacidade de exportação publicada. |

## 5. Histórico e comparação

```text
Histórico e comparação
Buscar [            ]  Perfil [Todos ▼]  Período [          ]
Seleção  Ensaio               Data         Dispositivos  Cenário           Estado
[x]      Ensaio referência    23/11/2026   50            baseline          Concluído
[x]      Ensaio temperatura   23/11/2026   50            temperature-rise  Concluído

[Comparar] [Reexecutar com a configuração salva] [Exportar relatório]
Comparação                    Ensaio A           Ensaio B           Referência de 50
Vazão (leituras/s)            [       ]          [       ]          24,955
CPU média                    [       ]          [       ]          0,738%
Pico RSS                     [       ]          [       ]          51,914 MiB
Configuração                 [Ver]              [Ver]
Hash do ensaio               [Copiar]           [Copiar]           [Ver método]
```

A comparação mostra diferenças de semente, cenário, quantidade, duração e versão
da engine. O relatório inclui a configuração congelada, os gráficos, os dados
medidos e o hash. A tela identifica medições ausentes ou de métodos diferentes.

| Situação | Resposta da tela |
| --- | --- |
| Histórico vazio | Mostrar a entrada para executar o primeiro ensaio. |
| Relatório em geração | Mostrar o formato e o destino selecionados, com progresso. |
| Erro de leitura ou exportação | Manter a seleção e mostrar o motivo e o destino escolhido. |

## 6. Configurações globais

```text
Configurações globais
Idioma                        [Português do Brasil ▼]
Fuso de exibição              [Local ▼]  Exportações [UTC]
Aviso de recursos acima de    [50] dispositivos
Atualização do painel         [250 ms ▼]
Presets e histórico           [Ver diretório de dados]
Credenciais                   [Abrir gerenciamento do cofre]
Diagnóstico                   Engine [v0.1.0]  Revisão [948342c]  Branch [main]
                              CI de referência [113 Windows · 113 Ubuntu] [Ver execução]
                              [Salvar]
```

A versão, a revisão e a branch descrevem a engine instalada quando disponíveis.
O resultado de CI é uma referência datada. A opção de idioma prepara EN no
catálogo de traduções, com PT-BR como padrão.

| Situação | Resposta da tela |
| --- | --- |
| Configuração ainda sendo lida | Mostrar **Carregando configurações**. |
| Cofre ausente ou bloqueado | Mostrar o estado do cofre e permitir nova tentativa. |
| Falha ao salvar | Manter os valores digitados e oferecer **Tentar novamente**. |
