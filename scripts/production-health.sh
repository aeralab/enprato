#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${ENPRATO_BASE_URL:-https://enprato.site}"
ENV_FILE="${ENPRATO_ENV_FILE:-/etc/enprato/enprato.env}"
[[ "$BASE_URL" == https://enprato.site* ]] || { echo "Refusing non-production HTTPS URL: $BASE_URL"; exit 2; }
[[ -r "$ENV_FILE" ]] || { echo "Cannot read $ENV_FILE; run this on the server with the service env file."; exit 1; }
grep -Eq '^ENPRATO_ALLOW_MOCK_PAY=(0)?$' "$ENV_FILE" || { echo "mock payment is not explicitly disabled"; exit 1; }
grep -Eq '^ENPRATO_ENABLE_DEV_MEMBERSHIP=(0)?$' "$ENV_FILE" || { echo "dev membership is not explicitly disabled"; exit 1; }

curl -fsS --proto '=https' --tlsv1.2 "$BASE_URL/" >/dev/null
curl -fsS --proto '=https' --tlsv1.2 "$BASE_URL/api/health" >/dev/null
me_code="$(curl -sS -o /dev/null -w '%{http_code}' --proto '=https' --tlsv1.2 "$BASE_URL/api/auth/me")"
[[ "$me_code" == 200 ]] || { echo "auth/me failed: HTTP $me_code"; exit 1; }
notify_code="$(curl -sS -o /dev/null -w '%{http_code}' --proto '=https' --tlsv1.2 -X POST -H 'Content-Type: application/json' --data '{}' "$BASE_URL/api/payments/wechat/notify")"
[[ "$notify_code" != 404 ]] || { echo "payment callback path is missing"; exit 1; }

if [[ -n "${ENPRATO_CHECK_EMAIL:-}" && -n "${ENPRATO_CHECK_PASSWORD:-}" ]]; then
  cookie_file="$(mktemp)"; trap 'rm -f "$cookie_file"' EXIT
  login_code="$(curl -sS -o /dev/null -w '%{http_code}' --proto '=https' --tlsv1.2 -c "$cookie_file" -H 'Content-Type: application/json' -d "{\"email\":\"$ENPRATO_CHECK_EMAIL\",\"password\":\"$ENPRATO_CHECK_PASSWORD\"}" "$BASE_URL/api/auth/login")"
  [[ "$login_code" == 200 ]] || { echo "login check failed: HTTP $login_code"; exit 1; }
  me_body="$(curl -fsS --proto '=https' --tlsv1.2 -b "$cookie_file" "$BASE_URL/api/auth/me")"
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); u=d.get("user") or {}; assert u.get("id") and (u.get("membership") or {}).get("status") in {"active","expired","none"}' "$me_body"
  echo "domain, HTTPS, API, login and authenticated membership endpoint: OK"
else
  echo "domain, HTTPS, API and callback route: OK; login/member check skipped (set ENPRATO_CHECK_EMAIL/PASSWORD temporarily)"
fi
