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

## Checklist por funcionalidade

- Contratos atualizados antes de handlers.
- Tipos em interfaces públicas.
- Testes unitários, de contrato e integração relevantes.
- Documentação de decisão e comportamento atualizada.
- Revisão e pipeline antes de merge.
