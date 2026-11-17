"""Local CA preparation and review. No certificate is trusted automatically."""
import hashlib
import ssl
import subprocess
import sys
from pathlib import Path


def certificate_path():
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


def inspect_certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    path = certificate_path()
    if not path.exists():
        return None
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    return {"path": str(path), "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "not_before": cert.not_valid_before_utc.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(),
            "sha256": cert.fingerprint(hashes.SHA256()).hex().upper()}


def is_trusted(info=None):
    info = info or inspect_certificate()
    if not info or sys.platform != "win32":
        return False
    return any(encoding == "x509_asn" and
               hashlib.sha256(der).hexdigest().upper() == info["sha256"]
               for der, encoding, _trust in ssl.enum_certificates("ROOT"))


def prepare_certificate():
    # A child isolates mitmproxy imports from Tk and does not start a proxy.
    code = ("from pathlib import Path; from mitmproxy.certs import CertStore; "
            "p=Path.home()/'.mitmproxy'; p.mkdir(exist_ok=True); "
            "CertStore.from_store(str(p), 'mitmproxy', 2048)")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("证书组件启动失败，请重新解压完整 GUI 安装包")
    return inspect_certificate()


def install_reviewed_certificate(sha256):
    info = inspect_certificate()
    if not info or info["sha256"] != sha256:
        return False, "证书已发生变化，请重新检查并确认指纹"
    result = subprocess.run(
        ["certutil", "-user", "-addstore", "Root", info["path"]],
        capture_output=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not is_trusted(info):
        return False, "证书未安装或未受信任；如已取消 Windows 提示，可重新点击安装"
    return True, "已确认此证书受 Windows 信任"
