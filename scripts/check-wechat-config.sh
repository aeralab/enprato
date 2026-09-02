#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/enprato/enprato.env}"
[[ -r "$ENV_FILE" ]] || { echo "Cannot read environment file: $ENV_FILE"; exit 1; }
get() { sed -n -E "s/^$1=(.*)$/\1/p" "$ENV_FILE" | tail -n 1; }
fail=0
for name in WECHATPAY_MCH_ID WECHATPAY_APP_ID WECHATPAY_MERCHANT_SERIAL_NO WECHATPAY_PUBLIC_KEY_ID WECHATPAY_PRIVATE_KEY_PATH WECHATPAY_PUBLIC_KEY_PATH WECHATPAY_API_V3_KEY WECHATPAY_NOTIFY_URL; do
  value="$(get "$name")"
  [[ -n "$value" ]] || { echo "missing: $name"; fail=1; }
done
[[ "$(get ENPRATO_ALLOW_MOCK_PAY)" != "1" ]] || { echo "mock payment must be disabled"; fail=1; }
[[ "$(get ENPRATO_ENABLE_DEV_MEMBERSHIP)" != "1" ]] || { echo "dev membership must be disabled"; fail=1; }
[[ "$(get ENPRATO_COOKIE_SECURE)" == "1" ]] || { echo "cookie secure flag must be 1"; fail=1; }
url="$(get WECHATPAY_NOTIFY_URL)"
[[ "$url" == https://enprato.site/* ]] || { echo "callback URL must use https://enprato.site"; fail=1; }
key="$(get WECHATPAY_API_V3_KEY)"
[[ -z "$key" || ${#key} -eq 32 ]] || { echo "WECHATPAY_API_V3_KEY must be 32 bytes"; fail=1; }
for name in WECHATPAY_PRIVATE_KEY_PATH WECHATPAY_PUBLIC_KEY_PATH; do
  path="$(get "$name")"; [[ -z "$path" || -r "$path" ]] || { echo "unreadable: $name"; fail=1; }
done
if ((fail)); then echo "WeChat production configuration check failed"; exit 1; fi
echo "WeChat production configuration shape is valid; secret values were not printed."
