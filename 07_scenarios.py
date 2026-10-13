"""Q8: сценарии P1b (платформа), P3 (внутренняя функция) и сверка -36 / -16,4 в движке finance.
Запуск:  cd /home/claude/product_farm && PYTHONPATH=src python3 projects/20261013-llm-budget-manager/07_scenarios.py
drivers.json НЕ изменяется: все сценарии - копии в памяти. Результат подставляется в 07_finance_scenarios.md
вместо маркера <!--ENGINE_RESULTS-->."""
import copy
import json
from pathlib import Path

from product_farm import finance as F

RUN = Path(__file__).resolve().parent
D0 = F.parse_drivers(json.loads((RUN / "drivers.json").read_text()))
M = 1e6


def mk(**over):
    """Копия драйверов: base-значения заменены на over (pess/opt не используются)."""
    d = copy.deepcopy(D0)
    for k, v in over.items():
        d[k]["base"] = float(v)
    return d


def npv(d, h):
    return F.simulate(d, "base", h).npv / M


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# ---------- A. Сверка -16,4 (red team) и -36 (финансист) ----------
RT = dict(launch_delay_months=6, dev_investment=9.5 * M, compliance_one_time=1.0 * M,
          compliance_annual=1.5 * M, cac=0.3 * M, variable_cost_share=0.15, fixed_costs_monthly=1.25 * M)
# допущения финансиста для «модуля» в отчёте не записаны; гипотеза: параметры базы, кроме фикса 1,2 и общего комплаенса
FIN = dict(fixed_costs_monthly=1.2 * M, compliance_one_time=1.0 * M, compliance_annual=1.5 * M)
bridge = [("0. P1b red team (воспроизведение; ожидание -16,4 / +16,8)", dict(RT))]
cur = dict(RT)
for name, upd in [("1. запуск М9 вместо М6", dict(launch_delay_months=9)),
                  ("2. инвестиции до запуска 30 вместо 9,5 млн", dict(dev_investment=30 * M)),
                  ("3. CAC 1,2 вместо 0,3 млн", dict(cac=1.2 * M)),
                  ("4. переменные 30% вместо 15%", dict(variable_cost_share=0.30)),
                  ("5. фикс 1,2 вместо 1,25 млн/мес", dict(fixed_costs_monthly=1.2 * M))]:
    cur = {**cur, **upd}
    bridge.append((name + " (накопительно)", dict(cur)))
rowsA = [(n, f"{npv(mk(**o), 36):.1f}", f"{npv(mk(**o), 60):.1f}") for n, o in bridge]
hi = {**RT, "compliance_one_time": 7.2 * M, "compliance_annual": 7.5 * M}
rowsA.append(("P1b red team, но комплаенс 7,2/7,5 вместо 1,0/1,5", f"{npv(mk(**hi), 36):.1f}", f"{npv(mk(**hi), 60):.1f}"))
rowsA.append(("Гипотеза финансиста: база + фикс 1,2 + комплаенс 1,0/1,5", f"{npv(mk(**FIN), 36):.1f}", f"{npv(mk(**FIN), 60):.1f}"))
A = table(["Шаг", "NPV36, млн ₽", "NPV60, млн ₽"], rowsA)


# ---------- B. P1b привязан к базе платформы ----------
def p1b_drv(price, launch, var=0.15, flow=0.0, growth=0.0):
    """launch - месяц первой продажи модуля. Команда работает с М7 (после MVP), поэтому
    лишние месяцы до первой продажи добавлены в инвестиции: 9,5 + (launch-6) * 1,25 млн."""
    dev = 9.5 * M + max(0, launch - 6) * 1.25 * M
    return mk(launch_delay_months=launch, dev_investment=dev, compliance_one_time=1.0 * M,
              compliance_annual=1.5 * M, cac=0.3 * M, variable_cost_share=var, fixed_costs_monthly=1.25 * M,
              price_per_month=price, new_customers_first_month=flow, new_customers_growth=growth)


def threshold(price, launch, h, var=0.15):
    """Продаж модуля в месяц (постоянный поток), при которых NPV = 0."""
    return F.failure_point(p1b_drv(price, launch, var, flow=0.5), h, "new_customers_first_month")


P3000, P1500 = 238_000, 119_000
rowsB, rowsN = [], []
for label, price, var in [("чек 3 млн ₽/год", P3000, .15), ("чек 1,5 млн ₽/год", P1500, .15),
                          ("чек 1,5 млн ₽/год, перем. 30%", P1500, .30)]:
    for launch in (12, 9, 18):
        for h in (36, 60):
            x = threshold(price, launch, h, var)
            rowsB.append((label, f"М{launch}", h, "—" if x is None else f"{x * 12:.1f}"))
B = table(["Чек", "Первая продажа модуля", "Горизонт, мес", "Порог: продаж модуля в год (постоянный поток)"], rowsB)
for label, price in [("3 млн", P3000), ("1,5 млн", P1500)]:
    x = threshold(price, 12, 60)
    if x is None:
        continue
    s = x * 12
    for a in (0.10, 0.20, 0.30):
        rowsN.append((label, f"{s:.1f}", f"{int(a * 100)}%", f"{s / a:.0f}"))
N = table(["Чек/год", "Порог, продаж в год (NPV60=0, первая продажа М12)", "Доля допродажи базы в год",
           "Нужно внешних клиентов платформы"], rowsN)

# NPV при заданной базе платформы N и доле допродажи a (первая продажа М12)
rowsC = []
for price, label in [(P3000, "3 млн"), (P1500, "1,5 млн")]:
    for n in (10, 20, 30, 50, 100):
        a = 0.2
        d = p1b_drv(price, 12, flow=n * a / 12)
        rowsC.append((label, n, f"{int(a * 100)}%", f"{n * a:.1f}", f"{npv(d, 36):.1f}", f"{npv(d, 60):.1f}",
                      f"{F.simulate(d, 'base', 60).end_customers:.1f}"))
C = table(["Чек/год", "Внешних клиентов платформы N", "Доля допродажи/год", "Продаж/год", "NPV36", "NPV60",
           "Активных модулей на М60"], rowsC)

# поток red team (рынок отдельного продукта): что он подразумевает
rt_d = mk(**RT)
r36, r60 = F.simulate(rt_d, "base", 36), F.simulate(rt_d, "base", 60)
s36 = sum(x["new"] for x in r36.months)
s60 = sum(x["new"] for x in r60.months)
Dtab = table(["Горизонт", "Активных модулей (поток red team)", "Продаж нарастающим итогом", "Средне продаж в год"],
             [("М36", f"{r36.end_customers:.1f}", f"{s36:.1f}", f"{s36 / 30 * 12:.1f}"),
              ("М60", f"{r60.end_customers:.1f}", f"{s60:.1f}", f"{s60 / 54 * 12:.1f}")])
x36 = F.failure_point(rt_d, 36, "new_customers_first_month")
x60 = F.failure_point(rt_d, 60, "new_customers_first_month")
tot = lambda x, k: x * ((1.03 ** k - 1) / 0.03)
rtth = (f"Порог red team: NPV36=0 при {x36:.3f} нов./мес. в первый месяц, NPV60=0 при {x60:.3f}. "
        f"При росте 3%/мес. это {tot(x60, 12):.1f} продаж в первый год и {tot(x60, 54):.1f} за 54 мес. "
        f"= {tot(x60, 54) / 54 * 12:.1f} в год в среднем.")


# ---------- C. P3 внутренняя функция ----------
def p3_drv(E, share, option):
    """Экономия как выручка: churn=1 => active = новые за месяц; new=1; price = экономия/мес. CAC и переменных нет."""
    if option == "oss":      # LiteLLM MIT / настройка: 1,0 млн разово, сопровождение 0,25 FTE x 0,48 млн
        kw = dict(launch_delay_months=3, dev_investment=1.0 * M, fixed_costs_monthly=0.12 * M)
    else:                    # свой модуль: 9,5 млн MVP, 1,25 млн/мес. (2 разработчика + 0,5 PO)
        kw = dict(launch_delay_months=6, dev_investment=9.5 * M, fixed_costs_monthly=1.25 * M)
    return mk(compliance_one_time=0, compliance_annual=0, cac=0, variable_cost_share=0, monthly_churn=1.0,
              new_customers_first_month=1, new_customers_growth=0, price_per_month=share * E / 12, **kw)


Es = (20 * M, 40 * M, 60 * M, 75 * M)
shares = (0.05, 0.10, 0.20, 0.30)
OPTS = (("oss", "OSS/LiteLLM"), ("own", "Свой модуль"))
P3 = table(["Вариант", "Доля экономии", "E=20", "E=40", "E=60", "E=75 млн ₽/год"],
           [(lab, f"{int(sh * 100)}%", *[f"{npv(p3_drv(E, sh, opt), 60):.1f}" for E in Es])
            for opt, lab in OPTS for sh in shares])
rowsBE = []
for opt, lab in OPTS:
    for sh in shares:
        cells = []
        for h in (36, 60):
            x = F.failure_point(p3_drv(75 * M, sh, opt), h, "price_per_month")
            cells.append("—" if x is None else f"{x * 12 / sh / M:.1f}")
        rowsBE.append((lab, f"{int(sh * 100)}%", *cells))
BE = table(["Вариант", "Доля экономии", "Порог расхода NPV36=0, млн ₽/год", "Порог расхода NPV60=0, млн ₽/год"], rowsBE)

report = "\n\n".join([
    "### A. Сверка (движок)\n\n" + A,
    "### B1. Порог P1b по привязке к платформе (движок)\n\n" + B,
    "### B2. Перевод порога в число внешних клиентов платформы (движок + деление)\n\n" + N,
    "### B3. NPV при заданной базе платформы (доля допродажи 20% в год, первая продажа М12)\n\n" + C,
    "### B4. Какой базе платформы соответствует поток red team (проверка)\n\n" + Dtab + "\n\n" + rtth,
    "### C1. P3: NPV60, млн ₽ (доход = экономия)\n\n" + P3,
    "### C2. P3: порог внутреннего расхода\n\n" + BE,
])
f = RUN / "07_finance_scenarios.md"
f.write_text(f.read_text().replace("<!--ENGINE_RESULTS-->", report))
print(report)
