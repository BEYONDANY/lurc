# AI-GEN-BEGIN
"""北森 iTalent SSO（OIDC JWT）适配层。

协议参考 ziliao/sso/Beisen.OIDC.SDK 与《单点登录北森SSO手册v2》，
本模块为 LEUC 原型自研实现，不引用 Java SDK。

配置来源：项目根目录 `.env` / 环境变量 `BEISEN_SSO_*`
（由 python-dotenv 加载，与 LeOrg 一致）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
DEFAULT_AUTH_URL = "https://oapi.italent.cn/SSO/AuthCenter"
# SSO 验票成功后落地北森门户（iTalent）
DEFAULT_RETURN_URL = "https://www.italent.cn/"
DEFAULT_APP_ID = "100"
DEFAULT_UTY = "id"
DEFAULT_TTL = 15 * 60
_DOTENV_LOADED = False


def _ensure_dotenv() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH, override=False)
    except ImportError:
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    _DOTENV_LOADED = True


@dataclass
class BeisenSsoConfig:
    tenant_id: str
    public_key: str
    private_key: str
    iss: str = "127.0.0.1:5055"
    app_id: str = DEFAULT_APP_ID
    uty: str = DEFAULT_UTY
    auth_url: str = DEFAULT_AUTH_URL
    return_url: str = DEFAULT_RETURN_URL
    ttl_seconds: int = DEFAULT_TTL

    @property
    def enabled(self) -> bool:
        return bool(
            self.tenant_id.strip()
            and self.public_key.strip()
            and self.private_key.strip()
        )


def _b64_std(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64_url_safe(std_b64: str) -> str:
    """对齐 SDK SafeTools.Base64StringToSafeBase64。"""
    return std_b64.replace("+", "-").replace("/", "_").replace("=", "")


def kid_from_public_key(public_key_pem: str) -> str:
    """公钥去掉换行后 SHA-256 十六进制（小写），对齐 SDK GetKid。"""
    compact = public_key_pem.replace("\r", "").replace("\n", "")
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def _load_private_key(pem: str):
    return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)


def build_id_token(
    *,
    public_key: str,
    private_key: str,
    iss: str,
    sub: str,
    aud: str,
    cls: dict[str, Any],
    iat: int | None = None,
    exp: int | None = None,
    ttl_seconds: int = DEFAULT_TTL,
) -> str:
    """生成北森 SSO 用 id_token（JWT，三段 url-safe base64）。"""
    now = int(time.time()) if iat is None else int(iat)
    expire = (now + int(ttl_seconds)) if exp is None else int(exp)

    header = {"alg": "RS256", "kid": kid_from_public_key(public_key)}
    payload = {
        "iss": iss,
        "sub": sub,
        "aud": str(aud),
        "exp": str(expire),
        "iat": str(now),
        "cls": cls,
    }
    header_json = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    h_b64 = _b64_std(header_json.encode("utf-8"))
    p_b64 = _b64_std(payload_json.encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("utf-8")

    key = _load_private_key(private_key)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = _b64_std(signature)

    return ".".join(
        [
            _b64_url_safe(h_b64),
            _b64_url_safe(p_b64),
            _b64_url_safe(sig_b64),
        ]
    )


def build_auth_url(
    id_token: str,
    *,
    auth_url: str = DEFAULT_AUTH_URL,
    return_url: str | None = None,
) -> str:
    """拼北森 AuthCenter 跳转地址。"""
    q: dict[str, str] = {"id_token": id_token}
    if return_url:
        q["return_url"] = return_url
    return f"{auth_url.rstrip('?')}?{urlencode(q, quote_via=quote)}"


def launch_url(
    cfg: BeisenSsoConfig,
    *,
    sub: str,
    uty: str | None = None,
    return_url: str | None = None,
) -> dict[str, Any]:
    """按配置签发 token 并返回跳转信息。"""
    if not cfg.enabled:
        raise ValueError("北森 SSO 未配置完整（BEISEN_SSO_TENANT_ID / PUBLIC_KEY / PRIVATE_KEY）")
    if not (sub or "").strip():
        raise ValueError("缺少登录标识 sub（邮箱 / BeisenUserID / 工号）")

    use_uty = (uty or cfg.uty or DEFAULT_UTY).strip()
    cls = {
        "appid": str(cfg.app_id or DEFAULT_APP_ID),
        "uty": use_uty,
        "url_type": "0",
        "isv_type": "0",
        "vsn": "1",
    }
    token = build_id_token(
        public_key=cfg.public_key,
        private_key=cfg.private_key,
        iss=cfg.iss,
        sub=sub.strip(),
        aud=cfg.tenant_id,
        cls=cls,
        ttl_seconds=cfg.ttl_seconds,
    )
    final_return = (return_url if return_url is not None else cfg.return_url) or None
    if final_return == "":
        final_return = None
    url = build_auth_url(token, auth_url=cfg.auth_url, return_url=final_return)
    return {
        "id_token": token,
        "redirect_url": url,
        "aud": cfg.tenant_id,
        "sub": sub.strip(),
        "uty": use_uty,
        "iss": cfg.iss,
        "appid": cls["appid"],
        "return_url": final_return,
    }


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _normalize_pem(raw: str) -> str:
    """PEM：支持 .env 中用 \\n 表示换行。"""
    if not raw:
        return ""
    text = raw.replace("\\n", "\n").strip()
    return text + ("\n" if text and not text.endswith("\n") else "")


def load_config() -> BeisenSsoConfig:
    _ensure_dotenv()
    ttl_raw = _env("BEISEN_SSO_TTL_SECONDS", str(DEFAULT_TTL))
    try:
        ttl = int(ttl_raw)
    except ValueError:
        ttl = DEFAULT_TTL

    return BeisenSsoConfig(
        tenant_id=_env("BEISEN_SSO_TENANT_ID"),
        public_key=_normalize_pem(os.environ.get("BEISEN_SSO_PUBLIC_KEY") or ""),
        private_key=_normalize_pem(os.environ.get("BEISEN_SSO_PRIVATE_KEY") or ""),
        iss=_env("BEISEN_SSO_ISS", "127.0.0.1:5055") or "127.0.0.1:5055",
        app_id=_env("BEISEN_SSO_APP_ID", DEFAULT_APP_ID) or DEFAULT_APP_ID,
        uty=_env("BEISEN_SSO_UTY", DEFAULT_UTY) or DEFAULT_UTY,
        auth_url=_env("BEISEN_SSO_AUTH_URL", DEFAULT_AUTH_URL) or DEFAULT_AUTH_URL,
        return_url=_env("BEISEN_SSO_RETURN_URL", DEFAULT_RETURN_URL)
        or DEFAULT_RETURN_URL,
        ttl_seconds=ttl,
    )


def status_dict(cfg: BeisenSsoConfig | None = None) -> dict[str, Any]:
    c = cfg or load_config()
    return {
        "enabled": c.enabled,
        "tenant_id": c.tenant_id or None,
        "app_id": c.app_id,
        "iss": c.iss,
        "uty": c.uty,
        "auth_url": c.auth_url,
        "has_public_key": bool(c.public_key.strip()),
        "has_private_key": bool(c.private_key.strip()),
        "config_file": str(ENV_PATH) if ENV_PATH.is_file() else None,
        "return_url": c.return_url or None,
    }


if __name__ == "__main__":
    cfg = load_config()
    print(json.dumps(status_dict(cfg), ensure_ascii=False, indent=2))
    if cfg.enabled:
        demo_sub = os.environ.get("BEISEN_SSO_DEMO_SUB") or "demo@example.com"
        out = launch_url(cfg, sub=demo_sub)
        print("sub=", out["sub"], "uty=", out["uty"])
        print("redirect_url=", out["redirect_url"][:120], "...")
# AI-GEN-END
