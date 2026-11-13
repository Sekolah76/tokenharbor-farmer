#!/usr/bin/env python3
"""ss2a_fast.py — ss2a.top 高速客户端：keep-alive 连接池 + 并发执行

为什么快：
  ss2a_client.api() 每次 urllib.request 都新建 TLS 连接，实测单次握手 ~700ms，
  单条请求中位 856ms。本模块每个线程复用一条长连接，单条降到 ~314ms；
  再叠加 ThreadPoolExecutor 并发（32 线程零 429），批量操作提速 10-20 倍。

用法：
    from ss2a_fast import api, pmap
    st, js = api("GET", "/api/v1/proxies", params={"page":1,"page_size":500})
    results = pmap(lambda p: api("POST", f"/api/v1/proxies/{p}/test"), ids, workers=16)

与 ss2a_client 的凭据/续期逻辑完全共用（同一份 .ss2a_creds.json），401 自动刷新（加锁）。
"""
import http.client
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
import sys

sys.path.insert(0, HERE)
from ss2a_client import BASE, refresh, save_creds, load_creds  # noqa: E402

HOST = BASE.replace("https://", "").replace("http://", "")
_ctx = ssl.create_default_context()
_local = threading.local()
_tok_lock = threading.Lock()
_token = {"value": None}


def _get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = http.client.HTTPSConnection(HOST, 443, timeout=60, context=_ctx)
        _local.conn = conn
    return conn


def _drop_conn():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def token() -> str:
    with _tok_lock:
        if _token["value"] is None:
            t, _, _ = refresh()
            _token["value"] = t
        return _token["value"]


def _refresh_locked():
    with _tok_lock:
        creds = load_creds()
        st, js = _raw_call("POST", "/api/v1/auth/refresh",
                           body={"refresh_token": creds["refresh_token"]},
                           token=None, _direct=True)
        data = (js or {}).get("data") or {}
        if st != 200 or not data.get("access_token"):
            raise RuntimeError(f"refresh failed HTTP {st}: {str(js)[:200]}")
        creds["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            creds["refresh_token"] = data["refresh_token"]
        creds["expires_at"] = int(time.time()) + int(data.get("expires_in") or 86400)
        save_creds(creds)
        _token["value"] = creds["access_token"]
        return creds["access_token"]


def _raw_call(method, path, body=None, token=None, params=None, timeout=60, _direct=False):
    if params:
        path += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token

    conn = _get_conn()
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        status = resp.status
        # 服务端关闭连接时 next 请求会报错，这里检测 Connection 头
        if resp.getheader("Connection", "").lower() == "close":
            _drop_conn()
        return status, (json.loads(raw) if raw.strip() else {})
    except (http.client.HTTPException, OSError) as exc:
        _drop_conn()
        raise exc


def api(method, path, body=None, params=None, timeout=60, retries=3):
    """高速 API 调用：线程本地长连接 + 断连自动重连 + 401 刷新重试"""
    last_exc = None
    for attempt in range(retries):
        try:
            st, js = _raw_call(method, path, body=body, token=token(), params=params, timeout=timeout)
            if st == 401:
                _refresh_locked()
                st, js = _raw_call(method, path, body=body, token=token(), params=params, timeout=timeout)
            return st, js
        except (http.client.HTTPException, OSError) as exc:
            last_exc = exc
            time.sleep(0.6 * (attempt + 1))
    return 0, {"raw": f"conn error: {last_exc}"}


def pmap(fn, items, workers=16, progress=None):
    """并发映射，保序返回；progress(i, total) 或 (完成数, 总数) 回调"""
    items = list(items)
    results = [None] * len(items)
    total = len(items)
    done = 0
    if workers <= 1 or total <= 1:
        for i, it in enumerate(items):
            results[i] = fn(it)
            done += 1
            if progress:
                progress(done, total)
        return results
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        try:
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:  # 单项失败不影响整批
                    results[i] = ("EXC", {"raw": str(exc)})
                done += 1
                if progress:
                    progress(done, total)
        finally:
            pass
    return results


def fetch_all_proxies(page_size=500):
    """拉全量代理（校验条数对得上 total，否则重试）"""
    for _ in range(6):
        st, js = api("GET", "/api/v1/proxies", params={"page": 1, "page_size": page_size})
        if st != 200:
            time.sleep(1.5)
            continue
        data = js.get("data", {})
        total = data.get("total", 0)
        items = list(data.get("items", []))
        page = 1
        while total and len(items) < total and page < 30:
            page += 1
            st, js = api("GET", "/api/v1/proxies", params={"page": page, "page_size": page_size})
            if st != 200:
                break
            items += js.get("data", {}).get("items", [])
        if not total or len(items) >= total:
            return items
        time.sleep(1.5)
    raise RuntimeError("拉代理列表失败")


if __name__ == "__main__":
    t = time.time()
    ps = fetch_all_proxies()
    print(f"拉全量 {len(ps)} 个代理，耗时 {(time.time()-t)*1000:.0f}ms")
    ids = [p["id"] for p in ps if (p.get("name") or "").startswith("proxyscrape-")]
    t = time.time()
    res = pmap(lambda i: api("POST", f"/api/v1/proxies/{i}/test"), ids[:20], workers=16)
    ok = sum(1 for st, js in res if st == 200 and (js.get("data") or {}).get("success"))
    print(f"并发测活 20 条：{(time.time()-t)*1000:.0f}ms，成功 {ok}")