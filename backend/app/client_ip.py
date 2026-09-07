"""Resolve the real client IP behind a single local Nginx reverse proxy.

Only headers from a trusted proxy peer (loopback) are honored. Direct clients
cannot spoof X-Forwarded-For / X-Real-IP to share or bypass SMS IP buckets.
"""
from __future__ import annotations

import ipaddress
from typing import Optional

from fastapi import Request

TRUSTED_PROXY_HOSTS = frozenset({"127.0.0.1", "::1"})


def _normalize_host(raw: str) -> str:
    value = (raw or "").strip().strip("[]")
    if value.lower().startswith("::ffff:"):
        mapped = value[7:]
        if _parse_ip(mapped):
            return mapped
    return value


def _parse_ip(raw: str) -> Optional[str]:
    value = (raw or "").strip().strip("[]")
    if not value:
        return None
    if value.count(":") == 1 and "." in value:
        host, _, port = value.rpartition(":")
        if port.isdigit():
            value = host
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    if ip.version == 6 and getattr(ip, "ipv4_mapped", None):
        return str(ip.ipv4_mapped)
    return str(ip)


def is_trusted_proxy(host: Optional[str]) -> bool:
    if not host:
        return False
    parsed = _parse_ip(_normalize_host(host))
    return parsed in TRUSTED_PROXY_HOSTS


def _header_value(request: Request, name: str) -> str:
    return request.headers.get(name, "") or ""


def _from_x_real_ip(request: Request) -> Optional[str]:
    return _parse_ip(_header_value(request, "x-real-ip"))


def _from_x_forwarded_for(request: Request) -> Optional[str]:
    raw = _header_value(request, "x-forwarded-for")
    if not raw:
        return None
    hops: list[str] = []
    for part in raw.split(","):
        parsed = _parse_ip(part)
        if parsed:
            hops.append(parsed)
    if not hops:
        return None
    for hop in reversed(hops):
        if hop not in TRUSTED_PROXY_HOSTS:
            return hop
    return hops[-1]


def peer_host(request: Request) -> str:
    if request.client and request.client.host:
        parsed = _parse_ip(_normalize_host(request.client.host))
        if parsed:
            return parsed
        return request.client.host.strip() or "unknown"
    return "unknown"


def resolve_client_ip(request: Request) -> str:
    peer = peer_host(request)
    if not is_trusted_proxy(peer):
        return peer
    return _from_x_real_ip(request) or _from_x_forwarded_for(request) or peer
