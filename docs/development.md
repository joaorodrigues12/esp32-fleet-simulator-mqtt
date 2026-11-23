# Desenvolvimento e qualidade

A branch `main` recebe apenas entregas com revisão e CI aprovado. Cada branch curta representa uma entrega. O título do pull request segue Conventional Commits.

## Ambiente

Use `uv` 0.13.9. O comando `uv python install` lê a versão de `.python-version` (3.12.8).
Execute `uv sync --locked --group dev` para instalar as dependências registradas em `uv.lock`.
Ao alterar dependências, rode `uv lock` e versione o lockfile junto com a configuração.

## Hooks

Instale os hooks com `uv run --locked pre-commit install --hook-type pre-commit --hook-type commit-msg`.
O hook `commit-msg` valida a mensagem antes de o Git registrar o commit.

## GitHub e CI

O GitHub Actions executa o workflow `CI` em pull requests e em pushes para `main` e branches `ci/**`.
O job `Qualidade e testes` roda no Ubuntu 24.04 e `Qualidade e testes (Windows)` roda no Windows Server 2025.
Ambos verificam lint e formatação com Ruff, tipagem com mypy e a suíte com pytest.
Avisos de depreciação do pytest fazem a etapa de testes falhar.
O Ubuntu instala o Mosquitto via APT. O Windows usa o pacote oficial x64, verifica seu SHA-256 e extrai os arquivos para o diretório temporário do runner.
Cada job configura `MOSQUITTO_EXECUTABLE` com o caminho validado antes de rodar a suíte.
Os testes de integração iniciam seus próprios brokers em portas temporárias no loopback.

Para incluir a integração na execução local, disponibilize `mosquitto` no PATH ou aponte
`MOSQUITTO_EXECUTABLE` para o executável. O pytest pula a integração local quando o broker está ausente.

Para reproduzir as verificações localmente, rode os mesmos comandos do CI:

```bash
uv python install
uv sync --locked --group dev
uv run --locked pre-commit run --all-files
uv run --locked pytest -v -W error::pytest.PytestDeprecationWarning
```

Confira o resultado em **Checks** no pull request ou em **Actions** para um push.

## Ensaio de recuperação do broker

Com o Mosquitto no PATH ou indicado por `MOSQUITTO_EXECUTABLE`, rode na raiz do projeto.

```console
uv run --locked pytest -q tests/integration/test_mqtt_fleet_recovery.py
```

O teste inicia um Mosquitto em uma porta temporária de `127.0.0.1` e confirma
presença e telemetria dos três dispositivos. Ele interrompe esse processo e
reinicia o broker na mesma porta após uma breve indisponibilidade, mantendo a
mesma CLI em execução.

Um novo assinante espera que os três dispositivos anunciem novos `boot_id` e
publiquem amostras geradas nessas conexões. Uma leitura reenviada com o
`boot_id` anterior mantém sua identidade de origem. O teste termina quando a
CLI completa os ciclos configurados e sai com código zero.

## Ensaio de carga

Com o Mosquitto no PATH ou indicado por `MOSQUITTO_EXECUTABLE`, rode na raiz do projeto o
comando abaixo em PowerShell ou Bash.

```console
uv run --locked python scripts/benchmark_fleet.py --devices 50 --samples 150 --interval 2
```

O script abre um broker próprio em uma porta temporária de `127.0.0.1`, assina
`vitals/+/data` com QoS 1 e então inicia a CLI com semente fixa. Os parâmetros acima
geram 150 leituras por dispositivo em cerca de cinco minutos, com taxa nominal de
25 mensagens de telemetria por segundo. O script aceita de 1 a 50 dispositivos.

O relatório JSON conta amostras únicas pelo `message_id`, duplicatas pelo mesmo
identificador e lacunas na faixa esperada de `sequence_number` de cada dispositivo.
Colisões de sequência e dispositivos com mais de um `boot_id` aparecem em campos
próprios. O script retorna código 1 se houver lacunas, colisões ou reconexões.

A vazão divide a contagem de amostras únicas pelo tempo entre o início e a saída da
CLI. Os percentis p50 e p95 medem o intervalo entre `sampled_at` e o momento em que o
assinante local lê a mensagem. Eles incluem o caminho de publicação, o broker e a
leitura pelo assinante no mesmo computador.

### Resultado de referência

Ensaio de 22/11/2026 em Windows (build 26200), com Python 3.12.8 e Mosquitto 2.1.4.
O resultado registra uma execução em loopback com a CLI, o broker e o assinante
no mesmo computador.

| Métrica | Valor |
| --- | ---: |
| Dispositivos configurados e observados | 50 de 50 |
| Amostras únicas recebidas | 7.500 de 7.500 |
| Lacunas, duplicatas e colisões de sequência | 0 em cada métrica |
| Dispositivos reconectados | 0 |
| Duração | 300,520 s |
| Vazão | 24,957 mensagens/s |
| Latência p50 | 7,509 ms |
| Latência p95 | 10,520 ms |

### Teste automatizado

Os testes automáticos verificam entrega e recursos com três dispositivos e quatro
leituras por dispositivo. Também verificam a retomada após reiniciar o Mosquitto e
o limite de 50 dispositivos.

```console
uv run --locked pytest -q tests/integration/test_fleet_benchmark.py
```

## Ensaio de recursos e tempo de recuperação

O benchmark de carga também usa `psutil` para amostrar, a cada 250 ms, a CPU e a
memória residente da árvore de processos da CLI. Isso inclui o interpretador
iniciado pelo executável da venv no Windows. O broker roda em processo próprio
e o assinante roda no processo do benchmark.
`fleet_cpu_seconds_sampled` soma os últimos tempos de CPU de usuário e sistema
observados por processo. `fleet_cpu_average_percent` divide esse total pela
duração da CLI. Um valor de 100% equivale a um processador lógico ocupado.
`fleet_peak_rss_mib` é o maior total de memória residente observado em uma coleta,
em MiB. Tanto o tempo de CPU quanto o pico de memória são estimativas por amostragem.

Para medir a retomada, rode o benchmark em modo de recuperação.

```console
uv run --locked python scripts/benchmark_fleet.py --restart-broker --devices 50 --samples 12 --interval 2
```

Depois de observar uma leitura de cada dispositivo, o script interrompe o
Mosquitto isolado, espera meio segundo e o reinicia na mesma porta. A medição
começa quando a verificação TCP confirma que a porta voltou a aceitar conexões.
Para cada dispositivo, o cronômetro termina na primeira leitura observada
pelo novo assinante com um novo `boot_id`. Essa medição depende do backoff
de reconexão, da cadência de coleta e do momento em que o assinante se conecta.
O JSON inclui os tempos por dispositivo,
p50, p95, máximo e o total recuperado. O script retorna código 1 se algum
dispositivo ficar sem leitura de retomada na janela de 12 segundos ou se a CLI falhar.

### Resultados com 50 dispositivos

Os dois ensaios foram executados em loopback em 22/11/2026 no Windows 11 (build
26200), com Python 3.12.8, Mosquitto 2.1.4 e psutil 7.2.2. A carga estável
usou 150 leituras por dispositivo e o ensaio de recuperação usou 12.
O intervalo foi de 2 segundos nos dois casos.

| Ensaio | Métrica | Valor |
| --- | --- | ---: |
| Carga estável | Leituras únicas recebidas | 7.500 de 7.500 |
| Carga estável | Duração | 300,547 s |
| Carga estável | Vazão | 24,955 mensagens/s |
| Carga estável | Coletas de recursos | 1.137 |
| Carga estável | Tempo de CPU amostrado | 2,219 s |
| Carga estável | CPU média de um processador lógico | 0,738% |
| Carga estável | Pico observado de memória residente | 51,914 MiB |
| Recuperação | Dispositivos com nova leitura | 50 de 50 |
| Recuperação | p50 | 1.047,914 ms |
| Recuperação | p95 | 1.048,879 ms |
| Recuperação | Máximo | 1.048,963 ms |

## Checklist por funcionalidade

- Contratos atualizados antes de handlers.
- Tipos em interfaces públicas.
- Testes unitários, de contrato e integração relevantes.
- Documentação de decisão e comportamento atualizada.
- Revisão e pipeline antes de merge.
