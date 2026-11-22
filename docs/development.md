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
O job `Qualidade e testes` roda em Ubuntu. Ele verifica lint e formatação com Ruff, tipagem com mypy e a suíte com pytest.
Avisos de depreciação do pytest fazem a etapa de testes falhar.
O job instala o Mosquitto e configura `MOSQUITTO_EXECUTABLE` com o caminho do executável.
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

O teste automático usa três dispositivos com quatro leituras cada. Para executá-lo
junto com a verificação do limite de 50 dispositivos, use o comando abaixo.

```console
uv run --locked pytest -q tests/integration/test_fleet_benchmark.py
```

## Checklist por funcionalidade

- Contratos atualizados antes de handlers.
- Tipos em interfaces públicas.
- Testes unitários, de contrato e integração relevantes.
- Documentação de decisão e comportamento atualizada.
- Revisão e pipeline antes de merge.
