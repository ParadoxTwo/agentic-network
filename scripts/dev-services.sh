#!/usr/bin/env bash
#
# Native Postgres + Redis for environments WITHOUT a Docker daemon
# (e.g. the web sandbox). Brings up the same logical services as
# docker-compose.yml — same user/db/port — so DATABASE_URL and REDIS_URL
# work identically whether you run compose (your machine/VPS) or this
# script (sandbox).
#
# On any machine with Docker, prefer `docker compose up -d` instead.
#
# Usage:
#   scripts/dev-services.sh up       # init (first run) + start both
#   scripts/dev-services.sh status   # readiness checks
#   scripts/dev-services.sh down     # stop both (keeps data)
#   scripts/dev-services.sh nuke     # stop + delete .devdata (wipes data)
#
# Connection strings once up:
#   DATABASE_URL=postgresql://agentic:devpass@127.0.0.1:5432/agentic
#   REDIS_URL=redis://127.0.0.1:6379/0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGDATA="$ROOT/.devdata/pg"
REDISDATA="$ROOT/.devdata/redis"
PGUSER="${POSTGRES_USER:-agentic}"
PGDB="${POSTGRES_DB:-agentic}"
PGPASS="${POSTGRES_PASSWORD:-devpass}"

# Postgres refuses to run as root; run its commands as the postgres user
# when we are root, otherwise as the current user.
if [ "$(id -u)" = "0" ]; then PG_AS="sudo -u postgres"; else PG_AS=""; fi

pg() { $PG_AS "$PGBIN/$1" "${@:2}"; }

up() {
  mkdir -p "$PGDATA" "$REDISDATA"
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    [ "$(id -u)" = "0" ] && chown -R postgres:postgres "$PGDATA"
    echo "initdb…"
    pg initdb -D "$PGDATA" -U "$PGUSER" --auth=trust >/tmp/initdb.log 2>&1
  fi
  if pg_isready -q -h 127.0.0.1 -p 5432 -U "$PGUSER" 2>/dev/null; then
    echo "postgres already running"
  else
    echo "starting postgres on 127.0.0.1:5432…"
    pg pg_ctl -D "$PGDATA" -l /tmp/pg.log \
      -o "-c listen_addresses=127.0.0.1 -p 5432" -w start
  fi
  createdb -h 127.0.0.1 -p 5432 -U "$PGUSER" "$PGDB" 2>/dev/null \
    && echo "created db $PGDB" || echo "db $PGDB exists"
  psql -h 127.0.0.1 -p 5432 -U "$PGUSER" -d "$PGDB" -q \
    -c "ALTER ROLE $PGUSER WITH PASSWORD '$PGPASS';"

  echo "starting redis on 127.0.0.1:6379…"
  redis-server --daemonize yes --appendonly yes --dir "$REDISDATA" \
    --bind 127.0.0.1 --port 6379 --logfile /tmp/redis.log
  status
}

status() {
  pg_isready -h 127.0.0.1 -p 5432 -U "$PGUSER" || true
  printf 'redis: '; redis-cli -h 127.0.0.1 -p 6379 ping || true
}

down() {
  pg pg_ctl -D "$PGDATA" -m fast stop 2>/dev/null && echo "postgres stopped" || echo "postgres not running"
  redis-cli -h 127.0.0.1 -p 6379 shutdown nosave 2>/dev/null && echo "redis stopped" || echo "redis not running"
}

nuke() { down || true; rm -rf "$ROOT/.devdata"; echo "wiped .devdata"; }

case "${1:-up}" in
  up) up ;;
  status) status ;;
  down) down ;;
  nuke) nuke ;;
  *) echo "usage: $0 {up|status|down|nuke}" >&2; exit 2 ;;
esac
