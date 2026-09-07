import unittest

from starlette.requests import Request

from backend.app.client_ip import resolve_client_ip


def _request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    raw = [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/auth/phone/send",
        "raw_path": b"/api/auth/phone/send",
        "query_string": b"",
        "headers": raw,
        "server": ("127.0.0.1", 18787),
        "scheme": "http",
    }
    if peer is not None:
        scope["client"] = (peer, 50000)
    return Request(scope)


class ResolveClientIpTests(unittest.TestCase):
    def test_trusted_proxy_uses_x_real_ip(self):
        request = _request("127.0.0.1", {"X-Real-IP": "203.0.113.10"})
        self.assertEqual(resolve_client_ip(request), "203.0.113.10")

    def test_trusted_proxy_ipv6_loopback_uses_x_real_ip(self):
        request = _request("::1", {"X-Real-IP": "203.0.113.11"})
        self.assertEqual(resolve_client_ip(request), "203.0.113.11")

    def test_trusted_proxy_prefers_x_real_ip_over_forwarded_for(self):
        request = _request(
            "127.0.0.1",
            {
                "X-Real-IP": "203.0.113.10",
                "X-Forwarded-For": "198.51.100.1, 203.0.113.10",
            },
        )
        self.assertEqual(resolve_client_ip(request), "203.0.113.10")

    def test_trusted_proxy_xff_uses_rightmost_untrusted_hop(self):
        request = _request("127.0.0.1", {"X-Forwarded-For": "198.51.100.1, 203.0.113.20"})
        self.assertEqual(resolve_client_ip(request), "203.0.113.20")

    def test_untrusted_peer_ignores_spoofed_headers(self):
        request = _request(
            "203.0.113.99",
            {
                "X-Real-IP": "198.51.100.7",
                "X-Forwarded-For": "198.51.100.7, 198.51.100.8",
            },
        )
        self.assertEqual(resolve_client_ip(request), "203.0.113.99")

    def test_testclient_peer_ignores_spoofed_headers(self):
        request = _request(
            "testclient",
            {"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "1.1.1.1"},
        )
        self.assertEqual(resolve_client_ip(request), "testclient")

    def test_malformed_headers_fallback_without_raising(self):
        request = _request(
            "127.0.0.1",
            {
                "X-Real-IP": "not-an-ip",
                "X-Forwarded-For": "???, also-bad, ",
            },
        )
        self.assertEqual(resolve_client_ip(request), "127.0.0.1")

    def test_malformed_xff_skips_junk_and_uses_last_valid(self):
        request = _request("127.0.0.1", {"X-Forwarded-For": "not-an-ip, 203.0.113.30"})
        self.assertEqual(resolve_client_ip(request), "203.0.113.30")

    def test_mapped_ipv6_loopback_is_trusted(self):
        request = _request("::ffff:127.0.0.1", {"X-Real-IP": "203.0.113.40"})
        self.assertEqual(resolve_client_ip(request), "203.0.113.40")

    def test_missing_client_falls_back_unknown(self):
        self.assertEqual(resolve_client_ip(_request(None, {"X-Real-IP": "1.2.3.4"})), "unknown")
