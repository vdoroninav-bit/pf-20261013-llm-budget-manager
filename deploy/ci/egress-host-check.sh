#!/usr/bin/env bash
# Проверка «нет исходящих соединений наружу» на уровне хоста (принцип IV, T088).
# Только для стенда QA: нужны root, iptables, Docker и поднятый стек (make up, T087).
#
# Как работает: для каждого контейнера compose-проекта в цепочку DOCKER-USER ставится переход
# в свою цепочку BM_EGRESS. В ней трафик в частные сети (RFC 1918, loopback, link-local) пропускается,
# а последнее правило считает пакеты во все остальные адреса. Затем прогоняется make smoke;
# ожидаемый счётчик — 0. Правила удаляются при любом выходе.
#
# Переменные: COMPOSE_PROJECT (по умолчанию bm), SMOKE_CMD (по умолчанию «make smoke»).
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-bm}"
SMOKE_CMD="${SMOKE_CMD:-make smoke}"
CHAIN="BM_EGRESS"

[[ "$(id -u)" -eq 0 ]] || { echo "egress-host-check: нужен root (стенд QA)"; exit 2; }
command -v iptables >/dev/null || { echo "egress-host-check: нет iptables"; exit 2; }
iptables -nL DOCKER-USER >/dev/null 2>&1 || { echo "egress-host-check: нет цепочки DOCKER-USER (Docker не запущен?)"; exit 2; }

mapfile -t IPS < <(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
  | xargs -r docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' | tr ' ' '\n' | sed '/^$/d' | sort -u)
[[ "${#IPS[@]}" -gt 0 ]] || { echo "egress-host-check: нет контейнеров проекта ${COMPOSE_PROJECT} (make up)"; exit 2; }

cleanup() {
  for ip in "${IPS[@]}"; do iptables -D DOCKER-USER -s "$ip" -j "$CHAIN" 2>/dev/null || true; done
  iptables -F "$CHAIN" 2>/dev/null || true
  iptables -X "$CHAIN" 2>/dev/null || true
}
trap cleanup EXIT

iptables -N "$CHAIN"
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8 169.254.0.0/16; do
  iptables -A "$CHAIN" -d "$net" -j RETURN
done
iptables -A "$CHAIN" -m comment --comment "bm-egress-counter" -j RETURN
for ip in "${IPS[@]}"; do iptables -I DOCKER-USER 1 -s "$ip" -j "$CHAIN"; done
echo "egress-host-check: контейнеры ${IPS[*]}; прогон: ${SMOKE_CMD}"

bash -c "$SMOKE_CMD"

PKTS="$(iptables -nvxL "$CHAIN" | awk '/bm-egress-counter/ {print $1}')"
echo "egress-host-check: пакетов наружу = ${PKTS}"
[[ "${PKTS}" == "0" ]] || { echo "egress-host-check: FAIL"; exit 1; }
echo "egress-host-check: OK"
