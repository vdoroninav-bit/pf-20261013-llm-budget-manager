---
status: proposed
---

# ADR-006. Исходящие соединения — только через модуль `egress` в `worker`, гарантия держится на коде и тесте

Принцип IV требует проверяемого нуля исходящих соединений при `IMPORT_ENABLED=false`. Единственный HTTP-клиент — `bm.egress`. В нём allowlist: `api.anthropic.com` и `api.openai.com` только при включённом флаге, хост `WEBHOOK_URL` только если он резолвится в частный диапазон. import-linter запрещает `httpx`, `urllib`, `http.client` и `socket` в остальных пакетах. Проверка двухуровневая: в `make test` подменяется `socket.connect`, и любое соединение, кроме PostgreSQL, роняет тест; на стенде QA счётчик в цепочке `DOCKER-USER` должен показать 0 пакетов наружу.

## Consequences

- Сеть Docker `internal: true` применяется только к `postgres` и `backup`. `api` и `worker` к ней тоже подключены, но не только к ней: `api` нужен опубликованный порт, а `worker` — webhook во внутреннюю сеть.
- При переносе в Kubernetes то же правило дополняется NetworkPolicy.

## Considered Options

- Изоляция `api` через `internal: true` и профиль `import` (ai, ADR-AI-5). Отвергнута: на сети `internal: true` контейнеру нужен опубликованный порт, а это противоречие [Вывод, [Docker docs](https://docs.docker.com/reference/compose-file/networks/); проверить на спайке, день 2]. Гарантия всё равно свелась бы к коду.
