#!/usr/bin/env python3
"""probe_signup_browser.py — 用 headless Chrome 真实提交注册，抓真实报文与响应。

用途：服务端开始回 `Please complete the human check` 但 precheck 说 false 时，
用真浏览器走一遍，确认到底缺什么（Turnstile token / 特殊 header / _cf 字段 / cookie）。

输出：/tmp/th_probe.json（请求头、multipart 字段、响应体）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import requests
import websocket

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
PROFILE = "/tmp/th-probe-prof"

HOOK = r"""
window.__reqs = [];
window.__resps = [];
const of = window.fetch;
window.fetch = function(input, init){
  try {
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    let bodyRepr = '';
    if (init && init.body instanceof FormData) {
      const parts = [];
      for (const [k,v] of init.body.entries()) parts.push(k + '=' + String(v).slice(0,200));
      bodyRepr = parts.join(' || ');
    }
    window.__reqs.push({
      url: url, method: (init && init.method) || 'GET',
      headers: init && init.headers ? JSON.stringify(init.headers) : '',
      body: bodyRepr, bodyType: init && init.body ? init.body.constructor.name : ''
    });
  } catch(e) { window.__reqs.push({err: String(e)}); }
  const p = of.apply(this, arguments);
  try {
    p.then(r => {
      r.clone().text().then(t => window.__resps.push({url:String(input), status:r.status, text:t.slice(0,3000)})).catch(()=>{});
    });
  } catch(e) {}
  return p;
};
"""


def cdp(ws, seq, method, params=None):
    seq[0] += 1
    ws.send(json.dumps({"id": seq[0], "method": method, "params": params or {}}))
    return seq[0]


def cdp_eval(ws, seq, expr, timeout=15):
    """发 Runtime.evaluate 并等待其 id 的结果（跳过事件消息）。"""
    mid = cdp(ws, seq, "Runtime.evaluate",
              {"expression": expr, "returnByValue": True, "awaitPromise": False})
    end = time.time() + timeout
    while time.time() < end:
        try:
            ws.settimeout(max(0.5, end - time.time()))
            msg = json.loads(ws.recv())
        except Exception:
            break
        if msg.get("id") != mid:
            continue          # 事件 / 其他响应，跳过
        res = msg.get("result") or {}
        if res.get("exceptionDetails"):
            return None
        return (res.get("result") or {}).get("value")
    return None


def main():
    if os.path.isdir(PROFILE):
        shutil.rmtree(PROFILE)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(40):
            time.sleep(0.4)
            try:
                tabs = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=3).json()
                page = next((t for t in tabs if t.get("type") == "page"), None)
                if page:
                    break
            except Exception:
                pass
        if not page:
            print("✗ Chrome 起不来"); return

        ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                         suppress_origin=True, timeout=40)
        seq = [0]
        for m in ("Page.enable", "Runtime.enable", "Network.enable"):
            cdp(ws, seq, m)
        cdp(ws, seq, "Network.clearBrowserCookies")
        cdp(ws, seq, "Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})

        email = f"probe{uuid.uuid4().hex[:10]}@hellocat.ccwu.cc"
        pwd = "ProbePwd123!Aa1"
        print("目标邮箱:", email)

        cdp(ws, seq, "Page.navigate", {"url": "https://tokenharbor.ai/login?mode=signup"})
        time.sleep(7)

        # 找 signup 表单的字段并填
        fill_js = """
        (function(){
          const inputs = [...document.querySelectorAll('input')];
          const info = inputs.map(i=>({name:i.name, type:i.type, id:i.id}));
          const setVal = (el, v) => {
            const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
            s.call(el, v);
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
          };
          const em = inputs.find(i=>i.name==='email');
          const pw = inputs.find(i=>i.name==='password');
          if (em) setVal(em, %EMAIL%);
          if (pw) setVal(pw, %PWD%);
          const forms = [...document.querySelectorAll('form')].map(f=>({
             action: f.action, method: f.method,
             fields: [...f.querySelectorAll('input')].map(i=>({n:i.name,t:i.type,v:String(i.value).slice(0,80)}))
          }));
          return JSON.stringify({inputs: info, forms: forms, hasEmail: !!em, hasPwd: !!pw});
        })()
        """.replace("%EMAIL%", json.dumps(email)).replace("%PWD%", json.dumps(pwd))

        info = cdp_eval(ws, seq, fill_js)
        print("表单状态:", (info or "?")[:400])
        time.sleep(1)

        # 提交
        submit_js = """
        (function(){
          const forms=[...document.querySelectorAll('form')];
          const f = forms.find(x=>x.querySelector('input[name=email]')) || forms[0];
          if(!f) return 'no form';
          if (f.requestSubmit) f.requestSubmit(); else f.submit();
          return 'submitted';
        })()
        """
        print("提交:", cdp_eval(ws, seq, submit_js))
        time.sleep(10)

        reqs_raw = cdp_eval(ws, seq, "JSON.stringify(window.__reqs || [])")
        resp_raw = cdp_eval(ws, seq, "JSON.stringify(window.__resps || [])")

        out = {"email": email,
               "reqs": json.loads(reqs_raw) if reqs_raw else [],
               "resps": json.loads(resp_raw) if resp_raw else []}
        json.dump(out, open("/tmp/th_probe.json", "w"), ensure_ascii=False, indent=1)

        print(f"\n=== 抓到 {len(out['reqs'])} 个请求 / {len(out['resps'])} 个响应 ===")
        for r in out["reqs"]:
            if "login" in (r.get("url") or "") or "signup" in (r.get("url") or ""):
                print(f"\n请求: {r.get('method')} {r.get('url')}")
                print("  headers:", (r.get("headers") or "")[:300])
                print("  bodyType:", r.get("bodyType"))
                print("  body:", (r.get("body") or "")[:900])
        for r in out["resps"]:
            if "login" in (r.get("url") or ""):
                print(f"\n响应 {r.get('status')}: {str(r.get('text'))[:600]}")
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
