# AI-GEN-BEGIN
"""LeOrg 部门/人员同步客户端（OAuth2 client_credentials）。

配置来源：项目根目录 `.env` / 环境变量 `LEORG_*`
（启动时由 python-dotenv 加载 `.env`）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://leorg-ai.lecoosys.com"
# emp:read_full：手机号不脱敏；需在 LeOrg 为该 client 开通，否则 token 会静默降级为 emp:read
DEFAULT_SCOPE = "org:read emp:read emp:read_full"
_DOTENV_LOADED = False


def _ensure_dotenv() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    env_path = ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        # 无 python-dotenv 时手写解析 .env（仅 KEY=VALUE）
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
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
class LeorgConfig:
    client_id: str
    client_secret: str
    base_url: str = DEFAULT_BASE_URL
    scope: str = DEFAULT_SCOPE

    @property
    def enabled(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())


def load_config() -> LeorgConfig | None:
    _ensure_dotenv()
    client_id = (os.environ.get("LEORG_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("LEORG_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return None
    return LeorgConfig(
        client_id=client_id,
        client_secret=client_secret,
        base_url=(os.environ.get("LEORG_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        scope=(os.environ.get("LEORG_SCOPE") or DEFAULT_SCOPE).strip(),
    )


def status_dict() -> dict[str, Any]:
    cfg = load_config()
    if not cfg:
        return {
            "ok": False,
            "enabled": False,
            "error": "未配置 LeOrg（复制 .env.example → .env，填写 LEORG_*）",
            "base_url": DEFAULT_BASE_URL,
        }
    out: dict[str, Any] = {
        "ok": True,
        "enabled": True,
        "base_url": cfg.base_url,
        "scope": cfg.scope,
        "client_id_suffix": cfg.client_id[-6:] if len(cfg.client_id) >= 6 else "***",
        "has_emp_read_full": "emp:read_full" in (cfg.scope or ""),
    }
    # AI-GEN-BEGIN
    # 探测实际换票 scope：未开通 emp:read_full 时 LeOrg 会静默降级，手机号仍脱敏
    try:
        client = LeorgClient(cfg)
        token_body = client._fetch_token_raw()
        granted = (token_body.get("scope") or "").strip()
        out["token_scope"] = granted
        out["token_has_emp_read_full"] = "emp:read_full" in granted
        if "emp:read_full" not in granted:
            out["phone_warning"] = (
                "当前 client 未获得 emp:read_full，LeOrg 返回脱敏手机号（如 136****1644），"
                "同步无法写入明文。请在 LeOrg 为该应用开通 scope：emp:read_full，"
                "并在 .env 的 LEORG_SCOPE 中包含 emp:read_full 后重同步。"
            )
            out["ok"] = True  # 连接仍可用，但手机同步不完整
    except Exception as exc:
        out["token_scope_error"] = str(exc)
    # AI-GEN-END
    return out


class LeorgClient:
    """换票 + 分页拉取 organizations / employees。"""

    def __init__(self, cfg: LeorgConfig | None = None):
        self.cfg = cfg or load_config()
        if not self.cfg or not self.cfg.enabled:
            raise RuntimeError("LeOrg 未配置")
        self._token: str | None = None
        self._token_exp: float = 0

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        url = self.cfg.base_url + path
        if query:
            qs = urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
            url = f"{url}?{qs}"
        hdrs = dict(headers or {})
        body = data
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if auth:
            hdrs["Authorization"] = f"Bearer {self._ensure_token()}"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LeOrg HTTP {e.code}: {err_body[:500]}") from e
        if not raw:
            return {}
        return json.loads(raw)

    def _fetch_token_raw(self) -> dict[str, Any]:
        """换票原始响应（含 scope），供状态探测。"""
        # AI-GEN-BEGIN
        return self._request(
            "POST",
            "/oauth/token",
            form={
                "grant_type": "client_credentials",
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "scope": self.cfg.scope,
            },
            auth=False,
        )
        # AI-GEN-END

    def _ensure_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        payload = self._fetch_token_raw()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"LeOrg 换票失败: {payload}")
        self._token = token
        self._token_exp = now + int(payload.get("expires_in") or 7200)
        self._token_scope = (payload.get("scope") or "").strip()
        return token

    def _paginate(
        self,
        path: str,
        *,
        extra_query: dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            q = {"page": page, "page_size": page_size}
            if extra_query:
                q.update(extra_query)
            env = self._request("GET", path, query=q)
            if not env.get("success", True) and env.get("code", 0) != 0:
                raise RuntimeError(env.get("message") or f"LeOrg 拉取失败: {path}")
            data = env.get("data") or {}
            batch = data.get("items") or []
            items.extend(batch)
            pag = data.get("pagination") or (env.get("meta") or {}).get("pagination") or {}
            total_pages = int(pag.get("total_pages") or 1)
            page += 1
            if not batch:
                break
        return items

    def list_organizations(self, *, status: int = 1) -> list[dict[str, Any]]:
        return self._paginate("/v1/organizations", extra_query={"status": status})

    def list_employees(self, *, emp_status: int | None = 1) -> list[dict[str, Any]]:
        q: dict[str, Any] = {}
        if emp_status is not None:
            q["emp_status"] = emp_status
        return self._paginate("/v1/employees", extra_query=q or None)

    def get_employee(self, emp_id: int) -> dict[str, Any] | None:
        env = self._request("GET", f"/v1/employees/{int(emp_id)}")
        data = env.get("data")
        if isinstance(data, dict) and data.get("emp_no") is not None:
            return data
        if isinstance(data, dict):
            return data.get("item") or data.get("employee") or data
        return None

    def get_organization(self, org_id: int) -> dict[str, Any] | None:
        env = self._request("GET", f"/v1/organizations/{int(org_id)}")
        data = env.get("data")
        if isinstance(data, dict) and data.get("name") is not None:
            return data
        if isinstance(data, dict):
            return data.get("item") or data.get("organization") or data
        return None

    def list_employee_changes(
        self, *, days: int = 7, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """拉取变更；接口按 id 倒序，读到 after_id 及更早即停（增量）。"""
        items: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        after_id = int(after_id or 0)
        while page <= total_pages:
            env = self._request(
                "GET",
                "/v1/employees/changes",
                query={"days": int(days), "page": page, "page_size": 100},
            )
            data = env.get("data") or {}
            batch = data.get("items") or []
            pag = data.get("pagination") or (env.get("meta") or {}).get("pagination") or {}
            total_pages = int(pag.get("total_pages") or 1)
            stop = False
            for row in batch:
                rid = int(row.get("id") or 0)
                if after_id and rid <= after_id:
                    stop = True
                    break
                items.append(row)
            if stop or not batch:
                break
            page += 1
        return items

    def latest_change_id(self, *, days: int = 1) -> int:
        env = self._request(
            "GET",
            "/v1/employees/changes",
            query={"days": int(days), "page": 1, "page_size": 1},
        )
        items = ((env.get("data") or {}).get("items")) or []
        if not items:
            return 0
        return int(items[0].get("id") or 0)

    def list_change_logs(
        self, *, entity_type: str | None = None
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {}
        if entity_type:
            q["entity_type"] = entity_type
        return self._paginate("/v1/change-logs", extra_query=q or None)
# AI-GEN-END
