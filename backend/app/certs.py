from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .remote_mic import lan_ipv4s

ROOT = Path(__file__).resolve().parents[1]
CERT_DIR = ROOT / "data" / "certs"
CERT_FILE = CERT_DIR / "lan.pem"
KEY_FILE = CERT_DIR / "lan-key.pem"


def ensure_lan_certs() -> tuple[Path, Path]:
    """Self-signed cert so iPhone can use mic (requires HTTPS)."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    ips = lan_ipv4s()
    names = ["localhost", "127.0.0.1", *ips]
    stamp = CERT_DIR / "sans.txt"
    wanted = ",".join(names)
    if CERT_FILE.is_file() and KEY_FILE.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == wanted:
        return CERT_FILE, KEY_FILE

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Enprato LAN")])
    alt = [x509.DNSName("localhost")]
    for ip in ["127.0.0.1", *ips]:
        try:
            alt.append(x509.IPAddress(__import__("ipaddress").ip_address(ip)))
        except Exception:
            alt.append(x509.DNSName(ip))

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    stamp.write_text(wanted, encoding="utf-8")
    return CERT_FILE, KEY_FILE
