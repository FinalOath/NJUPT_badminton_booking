"""Certificate audit and consent tests; never touch the real Windows root store."""
import datetime
from types import SimpleNamespace

import pytest

import certificate_manager as cm


def test_inspection_has_public_identity_and_fingerprint_only(tmp_path, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "test CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    path = tmp_path / "ca.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(cm, "certificate_path", lambda: path)
    info = cm.inspect_certificate()
    assert info["sha256"] == cert.fingerprint(hashes.SHA256()).hex().upper()
    assert info["subject"] == "CN=test CA"
    assert "PRIVATE" not in str(info)


def test_changed_certificate_cannot_be_installed(monkeypatch):
    monkeypatch.setattr(cm, "inspect_certificate", lambda: {"sha256": "new"})
    monkeypatch.setattr(cm.subprocess, "run", lambda *a, **k: pytest.fail("must not install"))
    assert cm.install_reviewed_certificate("reviewed")[0] is False


def test_install_is_user_scoped_and_requires_verified_trust(monkeypatch):
    monkeypatch.setattr(cm, "inspect_certificate", lambda: {"sha256": "reviewed", "path": "ca.pem"})
    calls = []
    monkeypatch.setattr(cm.subprocess, "run", lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(cm, "is_trusted", lambda info: False)
    assert cm.install_reviewed_certificate("reviewed")[0] is False
    assert calls[0] == ["certutil", "-user", "-addstore", "Root", "ca.pem"]


def test_trust_checks_exact_fingerprint_not_subject(monkeypatch):
    import hashlib
    monkeypatch.setattr(cm.sys, "platform", "win32")
    monkeypatch.setattr(cm.ssl, "enum_certificates", lambda store: [(b"other CA", "x509_asn", True)], raising=False)
    assert not cm.is_trusted({"sha256": hashlib.sha256(b"our CA").hexdigest().upper()})
    assert cm.is_trusted({"sha256": hashlib.sha256(b"other CA").hexdigest().upper()})
