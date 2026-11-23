"""Create the certificate SmartHR signs in to Microsoft 365 with (docs/SmartHR_Email_Setup.pdf, step 2).

Writes secrets/smarthr-key.pem (private key: stays on the SmartHR server, never shared or committed) and
secrets/smarthr.cer (public certificate: upload it to the Entra app registration), and prints the thumbprint.
Usage: .venv/Scripts/python scripts/make_cert.py [name]   (name: smarthr, the default, or website)
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

VALID_DAYS = 730  # Entra allows up to 2 years for an uploaded certificate; renew before it expires
OUT = Path(__file__).resolve().parent.parent / "secrets"

NAME = sys.argv[1] if len(sys.argv) > 1 else "smarthr"  # one certificate per app: smarthr, website
key_path, cert_path = OUT / f"{NAME}-key.pem", OUT / f"{NAME}.cer"
if key_path.exists():
    raise SystemExit(f"{key_path} already exists; delete it first if you really want a new certificate.")

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Kiaratech {NAME} mailbox app")])
now = datetime.now(timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=VALID_DAYS)).sign(key, hashes.SHA256()))

OUT.mkdir(exist_ok=True)
key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
print(f"Private key (keep secret): {key_path}")
print(f"Upload to Entra:           {cert_path}")
print(f"SMARTHR_CERT_THUMBPRINT=   {cert.fingerprint(hashes.SHA1()).hex().upper()}")
print(f"Expires:                   {cert.not_valid_after_utc:%Y-%m-%d}")
