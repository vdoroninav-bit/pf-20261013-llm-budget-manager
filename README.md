# Бюджет-менеджер по LLM-ресурсам — итог продуктовой фабрики

> **Статус: релиз v0.1.0 (QA: CONDITIONAL)** — коммит `d98ba2d`, 2026-10-21.
> Поставлены сценарии 1–6: решения, отчёты о расходе, настройка, подписки, импорт Anthropic и OpenAI, отчёты, роли, уведомления и webhook. MCP урезан на контрольной точке недели 5. FAIL нет.
> Условия: SC-001 — PASS-замена (замер не на эталонном стенде); лицензия `certifi` (MPL-2.0) ждёт юриста; на стенде заказчика не проверены SC-009, `egress-host-check` и форматы ответов провайдеров.
> Что поставлено, как запустить, гейты, ограничения и что нужно от заказчика — **[16_release.md](16_release.md)**. Установка — [docs/admin-guide.md](docs/admin-guide.md), интеграция — [docs/integration-guide.md](docs/integration-guide.md).

- **Вердикт CEO: PIVOT**
  - Отдельный продукт на рынок — **NO GO** (NPV 36 мес. −119,5 млн ₽, финансирование до 163,1 млн ₽); пересмотр только по итогам CustDev.
  - Платный модуль своей AI-платформы — **отложен, 0 ₽** до ≥ 10 внешних договоров платформы и ≥ 3 LOI на модуль.
  - Внутренняя функция на открытом LiteLLM — **GO с условиями**, до 4 млн ₽ под риском (P0 + первая версия).
- Проект решения CPO: PIVOT.

## Техническая команда: разработка и релиз

| Файл | Что внутри |
|---|---|
| [16_release.md](16_release.md) | **Релиз v0.1.0**: что поставлено, как запустить, итог QA, дефекты, ограничения, условия заказчику, стоимость |
| [15_qa_report.md](15_qa_report.md) | Отчёт QA, релизный прогон T089 и гейты (`QA: CONDITIONAL`) |
| [14_tech_rulings.md](14_tech_rulings.md) | Решения техлида по ходу разработки (пакеты 1–5) |
| [14_checkpoint_week5.md](14_checkpoint_week5.md) | Контрольная точка недели 5: webhook — в v1, MCP — урезан |
| [14_dev_backend.md](14_dev_backend.md) | Отчёт backend |
| [14_dev_frontend.md](14_dev_frontend.md) | Отчёт frontend |
| [14_devops.md](14_devops.md) | Отчёт DevOps: поставка, CI, SDL |
| [13_design.md](13_design.md), [13_design_rulings.md](13_design_rulings.md), [design/](design/) | Дизайн админки, решения по дизайну, экраны и токены |
| [12_approval_log.md](12_approval_log.md) | Согласование архитектуры заказчиком |
| [12_arch_decision.md](12_arch_decision.md) | Решение главного архитектора (гибрид на базе classic) |
| [12_adr/](12_adr/) | ADR-001 — ADR-007 |
| [12_plan.md](12_plan.md) | План реализации |
| [12_tasks.md](12_tasks.md) | Задачи T001–T099 со статусами |
| [12_openapi.yaml](12_openapi.yaml) | Контракт HTTP API (1.0.2) |
| [11_arch_classic.md](11_arch_classic.md), [11_arch_ai.md](11_arch_ai.md) | Варианты архитектуры |
| [10_tech_spec.md](10_tech_spec.md) | Техническая спецификация |
| [10_constitution.md](10_constitution.md) | Конституция проекта |
| [10_tech_brief_for_architects.md](10_tech_brief_for_architects.md) | Бриф для архитекторов |
| [10_tech_qa.md](10_tech_qa.md) | Уточнение требований с заказчиком |
| [docs/admin-guide.md](docs/admin-guide.md) | Руководство администратора (§14 — проверка поставки на своём стенде) |
| [docs/integration-guide.md](docs/integration-guide.md) | Руководство по интеграции вызывающих систем |
| [deploy/appimage/README.md](deploy/appimage/README.md) | **Linux AppImage**: запуск одним файлом без Docker (`make appimage`), состав и лицензии |
| [deploy/macos/README.md](deploy/macos/README.md) | **Mac-приложение** (Apple Silicon) без Docker (`make macos-app`), состав и лицензии |
| [deploy/ci/runs/d98ba2d.md](deploy/ci/runs/d98ba2d.md) | CI релизного SHA (аннотации check-run) |

Код: `app/backend` (Python, FastAPI), `app/frontend` (админка, react-admin), `app/client` (эталонный клиент `budget_client`), `deploy/` (Docker, compose, CI, SDL), `tests/`. Точка входа — `Makefile` (`make help`).

## Продуктовая команда: материалы

| Файл | Что внутри |
|---|---|
| [00_task.md](00_task.md) | Задача |
| [00_intake_qa.md](00_intake_qa.md) | Уточняющие вопросы CPO и ответы |
| [01_framing.md](01_framing.md) | Постановка (CPO) |
| [02_po.md](02_po.md) | Product Owner: MVP, эпик, метрики, гейты |
| [02_ba.md](02_ba.md) | Бизнес-аналитик: рынок, конкуренты, JTBD, бенчмарки |
| [02_legal.md](02_legal.md) | Юрист: светофор, реестр правовых рисков |
| [02_certification.md](02_certification.md) | Сертификация ПО (РФ): реестр, ФСТЭК, сроки, бюджеты |
| [02_finance.md](02_finance.md) | Финансист: вывод и логика драйверов |
| [02_finance_model.md](02_finance_model.md) | Финмодель — расчёт движка (3 сценария, чувствительность) |
| [finmodel.xlsx](finmodel.xlsx) | Финмодель с формулами (переключатель сценария) |
| [03_red_team.md](03_red_team.md) | Red team |
| [04_decision.md](04_decision.md) | Проект решения CPO и мемо для CEO |
| [05_ceo_grill.md](05_ceo_grill.md) | Допрос CEO: 3 раунда, ответы заказчика и команды |
| [07_finance_scenarios.md](07_finance_scenarios.md) | Сценарии «модуль» и «внутренняя функция» в движке |
| [07_ba_q7.md](07_ba_q7.md) | BA: бюджеты и квоты у конкурентов |
| [07_po_q11.md](07_po_q11.md) | PO: что закрывает LiteLLM (MIT) |
| [06_ceo_verdict.md](06_ceo_verdict.md) | **Заключение CEO** |

Рабочие файлы: `02_finance_draft.md` (черновик с JSON-драйверами), `drivers.json`, `07_scenarios.py`.
