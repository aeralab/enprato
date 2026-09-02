from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Protocol

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PaymentProvider(Protocol):
    name: str

    def create_native_payment(self, *, order_no: str, description: str, amount_fen: int) -> dict[str, Any]: ...
    def verify_and_decode_notify(self, *, headers: dict[str, str], body: bytes) -> dict[str, Any]: ...
    def query_order(self, *, order_no: str) -> dict[str, Any]: ...


class PaymentConfigError(RuntimeError):
    pass


class WechatPayProvider:
    name = "wechat"

    def __init__(self) -> None:
        self.mchid = os.environ.get("WECHATPAY_MCH_ID", "").strip()
        self.appid = os.environ.get("WECHATPAY_APP_ID", "").strip()
        self.serial = os.environ.get("WECHATPAY_MERCHANT_SERIAL_NO", "").strip()
        self.platform_serial = os.environ.get("WECHATPAY_PUBLIC_KEY_ID", "").strip()
        self.key_path = os.environ.get("WECHATPAY_PRIVATE_KEY_PATH", "").strip()
        self.platform_key_path = os.environ.get("WECHATPAY_PUBLIC_KEY_PATH", "").strip()
        self.notify_url = os.environ.get("WECHATPAY_NOTIFY_URL", "").strip()
        if not all((self.mchid, self.appid, self.serial, self.platform_serial, self.key_path, self.platform_key_path, self.notify_url)):
            raise PaymentConfigError("WeChat Pay environment is incomplete")
        self.private_key = serialization.load_pem_private_key(Path(self.key_path).read_bytes(), password=None)
        self.platform_key = self._load_public_key(Path(self.platform_key_path))

    @staticmethod
    def _load_public_key(path: Path):
        data = path.read_bytes()
        try:
            return serialization.load_pem_public_key(data)
        except ValueError:
            return x509.load_pem_x509_certificate(data).public_key()

    def _sign(self, message: bytes) -> str:
        return base64.b64encode(self.private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()

    def _authorization(self, method: str, path: str, body: str) -> str:
        nonce = secrets.token_urlsafe(16)
        timestamp = str(int(time.time()))
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n".encode()
        signature = self._sign(message)
        return f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mchid}",nonce_str="{nonce}",timestamp="{timestamp}",serial_no="{self.serial}",signature="{signature}"'

    def create_native_payment(self, *, order_no: str, description: str, amount_fen: int) -> dict[str, Any]:
        payload = {
            "appid": self.appid,
            "mchid": self.mchid,
            "description": description[:127],
            "out_trade_no": order_no,
            "notify_url": self.notify_url,
            "amount": {"total": amount_fen, "currency": "CNY"},
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        path = "/v3/pay/transactions/native"
        response = httpx.post(
            "https://api.mch.weixin.qq.com" + path,
            content=body.encode(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": self._authorization("POST", path, body),
            },
            timeout=20,
        )
        if response.status_code >= 300:
            raise RuntimeError(f"WeChat native order failed: HTTP {response.status_code}")
        data = response.json()
        if not data.get("code_url"):
            raise RuntimeError("WeChat native order did not return code_url")
        return {"provider": self.name, "code_url": data["code_url"]}

    def verify_and_decode_notify(self, *, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        timestamp = headers.get("Wechatpay-Timestamp", "")
        nonce = headers.get("Wechatpay-Nonce", "")
        signature = headers.get("Wechatpay-Signature", "")
        serial = headers.get("Wechatpay-Serial", "")
        if not timestamp or not nonce or not signature or serial != self.platform_serial:
            raise ValueError("Invalid WeChat callback identity")
        try:
            if abs(int(time.time()) - int(timestamp)) > 300:
                raise ValueError("WeChat callback timestamp expired")
        except ValueError:
            raise ValueError("Invalid WeChat callback timestamp")
        message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
        try:
            getattr(self, "platform_key", getattr(self, "platform_cert", None)).verify(base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:
            raise ValueError("Invalid WeChat callback signature") from exc
        envelope = json.loads(body)
        resource = envelope["resource"]
        key = os.environ.get("WECHATPAY_API_V3_KEY", "").encode()
        if len(key) != 32:
            raise PaymentConfigError("WECHATPAY_API_V3_KEY must be 32 bytes")
        plaintext = AESGCM(key).decrypt(
            resource["nonce"].encode(),
            base64.b64decode(resource["ciphertext"]),
            resource["associated_data"].encode(),
        )
        return json.loads(plaintext)

    def query_order(self, *, order_no: str) -> dict[str, Any]:
        path = f"/v3/pay/transactions/out-trade-no/{order_no}?mchid={self.mchid}"
        response = httpx.get(
            "https://api.mch.weixin.qq.com" + path,
            headers={"Accept": "application/json", "Authorization": self._authorization("GET", path, "")},
            timeout=20,
        )
        if response.status_code >= 300:
            raise RuntimeError(f"WeChat order query failed: HTTP {response.status_code}")
        return response.json()


def provider_for(name: str) -> PaymentProvider:
    if name == "wechat":
        return WechatPayProvider()
    raise PaymentConfigError("Unsupported payment provider")


def mock_provider_enabled() -> bool:
    return os.environ.get("ENPRATO_ALLOW_MOCK_PAY", "").lower() in {"1", "true", "yes"}
