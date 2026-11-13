#!/usr/bin/env python3
"""ss2a_client.py — ss2a.top 自动续期客户端（存一次 refresh_token，之后自动换 access_token）

凭据文件: .ss2a_creds.json（已加入 .gitignore，不进库；权限 600）
令牌逻辑:
  - access_token 有效期约 24h（JWT）
  - 快到期（默认剩 <300 秒）或收到 401 时，自动用 refresh_token 调 /api/v1/auth/refresh 换新
  - 每次刷新后把新 access_token / refresh_token / 到期时间写回凭据文件

CLI:
  python3 ss2a_client.py status      # 看 token 状态与剩余有效期
  python3 ss2a_client.py refresh     # 强制刷新一次（验证 refresh_token 是否有效）
  python3 ss2a_client.py accounts    # 顺手看一眼账号总数/分布

作为库使用:
  from ss2a_client import api
  st, js = api("GET", "/api/v1/accounts", params={"page": 1, "page_size": 5})
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(HERE, ".ss2a_creds.json")
BASE = "https://ss2a.top"


def load_creds():
    with open(CREDS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def save_creds(d):
    d["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = CREDS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CREDS_PATH)
    try:
        os.chmod(CREDS_PATH, 0o600)
    except OSError:
        pass


def _call(method, path, token=None, body=None, timeout=60):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:300]}
    except urllib.error.URLError as exc:
        return 0, {"raw": f"URLError: {exc.reason}"}


def refresh(creds=None, force=False, margin=300):
    """必要时刷新 token。返回 (access_token, creds, refreshed_bool)"""
    creds = creds or load_creds()
    now = int(time.time())
    if not force and (creds.get("expires_at") or 0) - now > margin:
        return creds["access_token"], creds, False
    st, js = _call("POST", "/api/v1/auth/refresh", body={"refresh_token": creds["refresh_token"]})
    data = js.get("data") or {}
    if st != 200 or not data.get("access_token"):
        raise SystemExit(f"❌ 刷新失败 HTTP {st}: {str(js)[:200]}")
    creds["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        creds["refresh_token"] = data["refresh_token"]
    creds["expires_at"] = int(time.time()) + int(data.get("expires_in") or 86400)
    save_creds(creds)
    return creds["access_token"], creds, True


def api(method, path, body=None, params=None, retry_auth=True, timeout=60):
    """带自动续期的 API 调用；401 会强制刷新后重试一次"""
    if params:
        path += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
    token, creds, _ = refresh()
    st, js = _call(method, path, token=token, body=body, timeout=timeout)
    if st == 401 and retry_auth:
        token, creds, _ = refresh(force=True)
        st, js = _call(method, path, token=token, body=body, timeout=timeout)
    return st, js


def _status():
    c = load_creds()
    left = int((c.get("expires_at") or 0) - time.time())
    print(f"账号: {c.get('email')} (user_id {c.get('user_id')})")
    print(f"access_token: {c['access_token'][:32]}…")
    print(f"refresh_token: {c['refresh_token'][:24]}…")
    print(f"到期: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(c.get('expires_at', 0)))}"
          f"（剩余 {left//3600}h{(left%3600)//60}m）")
    st, js = api("GET", "/api/v1/user/profile")
    print(f"连通性: HTTP {st} | {str(js.get('message', js))[:80]}" if st != 200 or js.get("code") else "连通性: ✅ 正常")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        _status()
    elif cmd == "refresh":
        t, c, did = refresh(force=True)
        print(f"{'✅ 已刷新' if did else '⏭ 未需要刷新'}")
        print(f"新 access_token: {t[:32]}… | 新到期时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(c['expires_at']))}")
        print(f"refresh_token: {c['refresh_token'][:24]}…")
    elif cmd == "accounts":
        st, js = api("GET", "/api/v1/accounts", params={"page": 1, "page_size": 1})
        print(f"HTTP {st} | 账号总数: {(js.get('data') or {}).get('total')}")
    else:
        print(__doc__)
