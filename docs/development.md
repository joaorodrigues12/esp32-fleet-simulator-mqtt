# Desenvolvimento e qualidade

A branch `main` recebe apenas entregas com revisão e pipeline aprovado. Cada branch `feature/*` representa uma funcionalidade comunicável. O título de PR segue Conventional Commits.

## Ambiente

Use `uv python install 3.12.8`, `uv lock`, `uv sync --group dev`. Versione `uv.lock` quando resolvido. Não misture Poetry e uv no mesmo projeto.

## Hooks

Instale `uv run pre-commit install --hook-type pre-commit --hook-type commit-msg` e execute `uv run pre-commit run --all-files`. Os hooks locais exigem dependências resolvidas; CI deve executar as mesmas verificações. O hook commit-msg aplica `Conventional Commits`.

## GitHub e CI

Criar o remoto `esp32-fleet-simulator-mqtt`, abrir PR para a scaffold e exigir status do CI antes de merge. Este ambiente não possui origem remota configurada; o merge local, quando feito, não substitui a aprovação do PR.

## Checklist por funcionalidade

- Contratos atualizados antes de handlers.
- Tipos em interfaces públicas.
- Testes unitários, de contrato e integração relevantes.
- Documentação de decisão e comportamento atualizada.
- Revisão e pipeline antes de merge.
