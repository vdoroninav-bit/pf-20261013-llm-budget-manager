# synthetic — фикстуры OpenAI `GET /v1/organization/costs` (T060)

Записанных ответов спайка (день 2) в репозитории нет, поэтому файлы `*.synthetic.json` собраны вручную.
Структура ответа сверена с типами официального SDK `openai` 3.35.1 (`types/admin/organization/usage_costs_response.py`,
`usage_costs_params.py`) [Факт]: страница `{object: "page", data: [bucket], has_more, next_page}`, корзина
`{object: "bucket", start_time, end_time, results}` (unix-секунды), строка
`{object: "organization.costs.result", amount: {value: float, currency: "usd"}, line_item, project_id}`;
параметры запроса `start_time`, `end_time`, `bucket_width=1d`, `group_by`, `page` (курсор из `next_page`).
Значения сумм и названия `line_item` — условные. После спайка заменить записанными ответами.

Содержимое: две страницы за сутки 2026-10-12 (1791763200) и 2026-10-13 (1791849600), UTC.
- 12.10: proj_analytics, gpt-4o, input 1,00 $.
- 13.10: proj_analytics, gpt-4o, input 1,50 + output 2,25 → 3,75 $; proj_marketing, gpt-4o-mini, input 0,12 $.
