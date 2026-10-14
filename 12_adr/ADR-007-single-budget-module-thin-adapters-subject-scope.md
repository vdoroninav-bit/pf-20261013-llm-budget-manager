---
status: proposed
---

# ADR-007. Один модуль `Budget` за HTTP, MCP и worker; субъекты привязаны к ключу

HTTP-роутеры, MCP-инструменты (`request_decision`, `report_usage`, `get_balance`) и `worker` вызывают одни и те же методы `Budget.decide/settle/balance/expire_reserves` с одними Pydantic-схемами. Поэтому коды и семантика совпадают по построению (FR-033), а тест эквивалентности гоняет один набор сценариев через оба адаптера. Каждый ключ вызывающей системы несёт `subject_scope`: `service`, `department_employees` (с `department_id`) или `any_employee`. Агент, которого склонили сослаться на чужого субъекта, получит 403 `subject_out_of_scope`, а попытка попадёт в аудит. Административных операций в MCP нет.

## Consequences

- Если MCP урежут, удаляется один пакет `bm.mcp` и его тест, логика остаётся.
- MCP монтируется в тот же ASGI-процесс на `/mcp`. API `mcp` 2.x проверяется на спайке.
