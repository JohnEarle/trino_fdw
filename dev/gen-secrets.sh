#!/usr/bin/env bash
# Generate a dev CA, a Trino server certificate and the password file.
set -euo pipefail
cd "$(dirname "$0")"
out=.generated
mkdir -p "$out/tls" "$out/pg"

if [ ! -f "$out/tls/ca.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=trino_fdw dev CA" \
    -keyout "$out/tls/ca.key" -out "$out/tls/ca.pem" >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -subj "/CN=trino" \
    -keyout "$out/tls/server.key" -out "$out/tls/server.csr" >/dev/null 2>&1
  printf "subjectAltName=DNS:trino,DNS:localhost,IP:127.0.0.1\n" > "$out/tls/san.cnf"
  openssl x509 -req -in "$out/tls/server.csr" -CA "$out/tls/ca.pem" -CAkey "$out/tls/ca.key" \
    -CAcreateserial -days 365 -extfile "$out/tls/san.cnf" -out "$out/tls/server.crt" >/dev/null 2>&1
  cat "$out/tls/server.key" "$out/tls/server.crt" > "$out/tls/server.pem"
fi
if [ ! -f "$out/tls/client.key" ]; then
  # client certificate for auth 'certificate'; CN becomes the Trino principal
  openssl req -newkey rsa:2048 -nodes -subj "/CN=svc_fdw" \
    -keyout "$out/tls/client.key" -out "$out/tls/client.csr" >/dev/null 2>&1
  openssl x509 -req -in "$out/tls/client.csr" -CA "$out/tls/ca.pem" -CAkey "$out/tls/ca.key" \
    -CAcreateserial -days 365 -out "$out/tls/client.crt" >/dev/null 2>&1
  # dev only: the bind mount must be readable by the container's postgres uid
  chmod 0444 "$out/tls/client.key"
fi

# RS256 JWT for auth 'jwt': Trino verifies with the public key, principal = sub
mkdir -p "$out/jwt"
if [ ! -f "$out/jwt/public.pem" ]; then
  openssl genrsa -out "$out/jwt/private.pem" 2048 >/dev/null 2>&1
  openssl rsa -in "$out/jwt/private.pem" -pubout -out "$out/jwt/public.pem" >/dev/null 2>&1
fi
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
exp=$(( $(date +%s) + 86400 ))
hdr=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
pl=$(printf '{"sub":"svc_fdw","aud":"trino","exp":%d}' "$exp" | b64url)
sig=$(printf '%s.%s' "$hdr" "$pl" | openssl dgst -sha256 -sign "$out/jwt/private.pem" | b64url)
rm -f "$out/pg/token"; printf '%s.%s.%s\n' "$hdr" "$pl" "$sig" > "$out/pg/token"; chmod 0444 "$out/pg/token"

SVC_PASSWORD="${SVC_PASSWORD:-svc-dev-password}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin-dev-password}"
htpasswd -nbB -C 10 svc_fdw "$SVC_PASSWORD"  >  "$out/password.db"
htpasswd -nbB -C 10 admin   "$ADMIN_PASSWORD" >> "$out/password.db"
rm -f "$out/pg/password"
printf "%s\n" "$SVC_PASSWORD" > "$out/pg/password"
# 0444 so the bind mount is readable inside the container regardless of uid mapping;
# the wrapper logs a warning for that, which is expected in the dev stack.
chmod 0444 "$out/pg/password"
echo "generated under dev/$out"
