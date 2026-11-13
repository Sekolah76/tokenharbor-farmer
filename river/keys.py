#!/usr/bin/env python3
"""river/keys.py — console.river.ai API Key 管理

接口家族（从 console bundle 的 Et() 封装层反出）：
    GET  /api/keys                 列表     MGe
    POST /api/keys                 创建     PGe
    PUT  /api/keys/{id}            改名     jGe
    POST /api/keys/{id}/revoke     吊销     DGe
    GET  /api/keys/propagation/{key_hash}/wait?timeout_secs=N   传播状态  $Ge

鉴权三要素（见 bundle 里的 zS()）：
    Authorization: Bearer <WorkOS access_token JWT>
    X-River-Team:  <team_id>       个人账号为 null，不发此头
    x-dev-act-as-email: <email>    dev 模式后门头，dev_login=false 时不应被认

明文 key 只在创建响应里出现一次，之后列表只给 key_hash。
"""
import time

from .client import CONSOLE, log


class KeysError(Exception):
    pass


class KeyManager:
    def __init__(self, session, access_token, team_id=None, timeout=40):
        self.s = session
        self.token = access_token
        self.team_id = team_id
        self.timeout = timeout

    # ------------------------------------------------------------ 内部

    def _headers(self, json_body=True):
        h = {"Authorization": f"Bearer {self.token}",
             "Accept": "application/json"}
        if json_body:
            h["Content-Type"] = "application/json"
        if self.team_id:
            h["X-River-Team"] = self.team_id
        return h

    def _call(self, method, path, **kw):
        r = self.s.request(method, f"{CONSOLE}/api{path}",
                           headers=self._headers(), timeout=self.timeout, **kw)
        if not r.ok:
            body = r.text[:300]
            try:
                body = r.json()
            except Exception:
                pass
            raise KeysError(f"{method} {path} -> {r.status_code}: {body}")
        return r.json()

    # ------------------------------------------------------------ CRUD

    def list(self):
        """返回 key 列表（不含明文）。"""
        return self._call("GET", "/keys")

    def create(self, name, description=None, expires_in_days=None,
               scheduling_priority=None):
        """POST /api/keys

        name                 必填（空 body 服务端 422 missing field `name`）
        description          可选，空串会整体省略
        expires_in_days      可选 int，不填 = 永不过期
        scheduling_priority  best_effort | standard，仅对特定 team 开放（bundle 里硬编码了
                             team 05bc4bc0-9c14-4ca3-900a-053ba083ceb5），其他账号可硬塞
        """
        body = {"name": name}
        if description:
            body["description"] = description
        if expires_in_days is not None:
            body["expires_in_days"] = int(expires_in_days)
        if scheduling_priority:
            body["scheduling_priority"] = scheduling_priority
        return self._call("POST", "/keys", json=body)

    def rename(self, key_id, name, description=None):
        body = {"name": name}
        if description:
            body["description"] = description
        return self._call("PUT", f"/keys/{key_id}", json=body)

    def revoke(self, key_id):
        return self._call("POST", f"/keys/{key_id}/revoke")

    # ------------------------------------------------------------ 传播

    def wait_propagation(self, key_hash, timeout_secs=60):
        """轮询密钥在推理网关的下发进度。

        River 是多节点部署，新 key 要下发到所有 api.river.ai 网关才生效。
        返回 { fully_propagated, confirmed_count, expected_count }。
        注意 key_hash 明文拼在 URL 路径里，会进 CDN/nginx access log。
        """
        r = self._call("GET", f"/keys/propagation/{key_hash}"
                             f"/wait?timeout_secs={timeout_secs}")
        return r

    def create_and_wait(self, name, description=None, expires_in_days=None,
                        scheduling_priority=None, timeout_secs=60):
        """创建 + 等待传播完成。返回 (创建响应, 传播状态)。"""
        created = self.create(name, description, expires_in_days, scheduling_priority)
        key_hash = created.get("key_hash")
        if not key_hash:
            log(f"创建响应里没有 key_hash，字段= {sorted(created)}", "WARN")
            return created, None

        status = None
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            try:
                status = self.wait_propagation(key_hash, timeout_secs=min(15, timeout_secs))
            except KeysError as e:
                log(f"传播查询失败：{e}", "WARN")
                break
            if status.get("fully_propagated"):
                break
            time.sleep(1)

        if status:
            log(f"传播 {status.get('confirmed_count')}/{status.get('expected_count')} "
                f"fully_propagated={status.get('fully_propagated')}")
        return created, status


# ---------------------------------------------------------------- me


def get_me(session, access_token, team_id=None):
    """GET /api/me —— 未认证时返回全 null 的 200，正确行为。"""
    h = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if team_id:
        h["X-River-Team"] = team_id
    return session.get(f"{CONSOLE}/api/me", headers=h, timeout=30).json()