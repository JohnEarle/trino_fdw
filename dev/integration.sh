#!/usr/bin/env bash
# End-to-end test: build the image, start Trino + PostgreSQL, seed Trino,
# define the FDW objects, and assert query results, pushdown, impersonation
# and the catalog guard. Requires docker compose, openssl and htpasswd.
set -euo pipefail
cd "$(dirname "$0")"

./gen-secrets.sh
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
docker compose up -d --build --wait

echo "== seeding Trino"
docker compose exec -T -e TRINO_PASSWORD=admin-dev-password trino \
  trino --server https://localhost:8443 --insecure --user admin --password -f /seed.sql

psql() { docker compose exec -T -e PGPASSWORD="$2" postgres psql -v ON_ERROR_STOP=1 -qtAX -U "$1" -d postgres "${@:3}"; }

echo "== waiting for PostgreSQL"
for i in $(seq 1 60); do docker compose exec -T postgres pg_isready -U postgres -q && break; sleep 1; done

echo "== defining FDW objects"
psql postgres postgres -f /pg/setup.sql

check() {  # check <label> <expected> <sql> [user] [password]
  local got
  got=$(psql "${4:-reader}" "${5:-reader}" -c "$3" | tr -d '\r')
  if [ "$got" = "$2" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$2] got [$got]"; exit 1; fi
}

check "full scan"            "4"            "SELECT count(*) FROM audit_events"
check "bigint > pushdown"    "3,4"          "SELECT string_agg(id::text, ',' ORDER BY id) FROM audit_events WHERE id > 2"
check "text filter"          "jsmith"       "SELECT DISTINCT username FROM audit_events WHERE username = 'jsmith'"
check "timestamp filter"     "3"            "SELECT count(*) FROM audit_events WHERE event_time > '2026-10-08 10:04:00+00'"
check "order by desc"        "4"            "SELECT id FROM audit_events ORDER BY event_time DESC LIMIT 1"
check "IN list"              "2"            "SELECT count(*) FROM audit_events WHERE action IN ('login')"
check "bigint > int4 range"  "0"            "SELECT count(*) FROM audit_events WHERE id > 3000000000"
check "boolean"              "1,3"          "SELECT string_agg(id::text, ',' ORDER BY id) FROM audit_events WHERE ok"
check "boolean = false"      "2,4"          "SELECT string_agg(id::text, ',' ORDER BY id) FROM audit_events WHERE ok = false"
check "numeric"              "3"            "SELECT id FROM audit_events WHERE amount > 50.00"
check "double"               "3,4"          "SELECT string_agg(id::text, ',' ORDER BY id) FROM audit_events WHERE score >= 0.5"
check "date"                 "2"            "SELECT count(*) FROM audit_events WHERE day = '2026-10-09'"
check "timestamptz tz input" "3"            "SELECT count(*) FROM audit_events WHERE event_time > '2026-10-08 12:04:00+02'"
check "json column"          "10.0.0.2"     "SELECT details->>'ip' FROM audit_events WHERE id = 3"
check "is null / not null"   "4"            "SELECT count(*) FROM audit_events WHERE username IS NOT NULL AND details IS NOT NULL"
check "limit pushdown"       "1"            "SELECT id FROM audit_events ORDER BY id LIMIT 1"
check "offset pushdown"      "3"            "SELECT id FROM audit_events ORDER BY id OFFSET 2 LIMIT 1"
check "join local table"     "jsmith"       "SELECT a.username FROM audit_events a JOIN (VALUES (1)) v(id) ON v.id = a.id"
check "uuid pushdown"        "3"            "SELECT id FROM audit_events WHERE uid = '33333333-3333-4333-8333-333333333333'"
check "inet cast pushdown"   "3,4"          "SELECT string_agg(id::text, ',' ORDER BY id) FROM audit_events WHERE ip = '10.0.0.2'"
check "inet cidr local"      "4"            "SELECT count(*) FROM audit_events WHERE ip << '10.0.0.0/24'"
check "jsonb local filter"   "2"            "SELECT count(*) FROM audit_events WHERE details = '{\"ip\":\"10.0.0.2\"}'::jsonb"
check "numeric exact"        "3"            "SELECT id FROM audit_events WHERE amount = 99.99"
check "numeric >15 digits"   "0"            "SELECT count(*) FROM audit_events WHERE amount = 1234567890123456.78"
check "impersonation"        "reader"       "SELECT u FROM trino_whoami"
check "no impersonation"     "svc_fdw"      "SELECT u FROM trino_whoami" postgres postgres
check "certificate auth"     "svc_fdw"      "SELECT u FROM whoami_cert" postgres postgres
check "certificate + imp."   "reader"       "SELECT u FROM whoami_cert"
check "certificate scan"     "4"            "SELECT count(*) FROM events_cert WHERE id > 0"
check "jwt auth"             "svc_fdw"      "SELECT u FROM whoami_jwt" postgres postgres
check "jwt + impersonation"  "reader"       "SELECT u FROM whoami_jwt"

echo "== explain output"
plan=$(psql postgres postgres -c "EXPLAIN (VERBOSE) SELECT id FROM audit_events WHERE id > 2 ORDER BY id")
echo "$plan" | sed 's/^/   /'
if echo "$plan" | grep -q '"id" > ?' && echo "$plan" | grep -q 'ORDER BY "id"'; then
  echo "ok   WHERE and ORDER BY pushed down"
else
  echo "FAIL pushdown not visible in plan"; exit 1
fi

echo "== import foreign schema"
psql postgres postgres -c "CREATE SCHEMA imported; IMPORT FOREIGN SCHEMA demo FROM SERVER trino INTO imported" >/dev/null
check "imported table"       "4"            "SELECT count(*) FROM imported.audit_events" postgres postgres
check "imported types"       "bigint,timestamp with time zone,character varying,character varying,boolean,numeric,double precision,date,jsonb,uuid,inet" \
  "SELECT string_agg(data_type, ',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='imported' AND table_name='audit_events'" postgres postgres
check "imported pushdown"    "3,4"          "SELECT string_agg(id::text, ',' ORDER BY id) FROM imported.audit_events WHERE event_time > '2026-10-08 10:06:00+00'" postgres postgres

echo "== password rotation on a live session"
fifo=$(mktemp -u); mkfifo "$fifo"; exec 3<>"$fifo"
out=$(mktemp)
docker compose exec -T -e PGOPTIONS="-c client_min_messages=error" postgres \
  psql -U reader -d postgres -qtAX <&3 >"$out" 2>&1 &
echo "SELECT u FROM trino_whoami;" >&3; sleep 3
htpasswd -nbB -C 10 svc_fdw "rotated-$$" > .generated/password.db
htpasswd -nbB -C 10 admin "admin-dev-password" >> .generated/password.db
chmod u+w .generated/pg/password
printf "rotated-%s\n" "$$" > .generated/pg/password   # in place: the bind mount tracks the inode
chmod 0444 .generated/pg/password
sleep 8   # Trino re-reads password.db every 5s
echo "SELECT u FROM trino_whoami;" >&3; sleep 3
echo "\\q" >&3; wait; exec 3>&-; rm -f "$fifo"
if [ "$(tr -d '\r' < "$out")" = $'reader\nreader' ]; then
  echo "ok   rotated password picked up by the cached connection"
else
  echo "FAIL rotation: got [$(cat "$out")]"; exit 1
fi

echo "== guard: secret in SQL must be refused"
if psql postgres postgres -c "CREATE USER MAPPING FOR reader SERVER trino OPTIONS (password 'x')" 2>/dev/null; then
  echo "FAIL guard did not fire"; exit 1
else
  echo "ok   guard refused CREATE USER MAPPING with password"
fi
if psql postgres postgres -c "ALTER SERVER trino OPTIONS (ADD token 'x')" 2>/dev/null; then
  echo "FAIL guard did not fire on ALTER SERVER"; exit 1
else
  echo "ok   guard refused ALTER SERVER with token"
fi

echo "== all integration checks passed"
