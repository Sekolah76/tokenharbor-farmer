/**
 * cf-mail worker — 自建收信服务（Cloudflare Email Routing + KV）
 *
 * 作用：
 *   email()  → 收到任意 <前缀>@<你的域名> 的信，原样存进 KV（catch-all，无需预先建箱）
 *   fetch()  → 给我们自己的脚本提供读信接口
 *
 * 部署：
 *   1. 域名挂到 Cloudflare，Email Routing 里把 Catch-all 指向这个 Worker
 *   2. wrangler kv namespace create MAIL   → 把 id 填进 wrangler.toml
 *   3. wrangler secret put MAIL_TOKEN      → 设一个读信口令（脚本用同一个）
 *   4. wrangler deploy
 *
 * 读信接口（都要带 X-Mail-Token）：
 *   GET /health                      → {"ok":true}
 *   GET /inbox?to=<地址>              → 列出该地址的信（最新的在前）
 *   GET /inbox?to=<地址>&grep=<文本>  → 只返回正文含该文本的信（脚本用它抓验证链接）
 *   GET /message?id=<key>            → 取单封全文
 *   DELETE /inbox?to=<地址>           → 清空该地址（可选，避免 KV 堆积）
 *
 * 免费额度参考：Workers 10 万请求/天，KV 读 10 万/天、写 1000/天。
 * 按我们每次注册 1-2 封邮件算，1000 写/天 ≈ 500+ 个账号，够用。
 */

const TTL = 60 * 60 * 24; // 邮件保留 24h，自动清理

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function authed(req, env) {
  const tok = req.headers.get("X-Mail-Token") || "";
  const url = new URL(req.url);
  const q = url.searchParams.get("token") || "";
  // 用 Pages/Worker 的 secret 比对；没设置就直接拒绝
  return !!env.MAIL_TOKEN && (tok === env.MAIL_TOKEN || q === env.MAIL_TOKEN);
}

/** 把 MIME 原文里能看到的文本扒出来（够用即可 —— 我们只要验证链接） */
function extractBody(raw) {
  let body = raw;

  // 如果有 multipart，优先取 text/plain 或 text/html 段
  if (/content-type:\s*multipart/i.test(raw)) {
    const parts = raw.split(/--[^\r\n]+/);
    const texts = parts.filter((p) => /content-type:\s*text\/(plain|html)/i.test(p));
    if (texts.length) {
      const plain = texts.find((p) => /content-type:\s*text\/plain/i.test(p));
      body = plain || texts[0];
    }
  }

  // 去掉头部，剩下的当正文（headers 与 body 之间是空行）
  const idx = body.search(/\r?\n\r?\n/);
  if (idx >= 0) body = body.slice(idx).trim();

  // quoted-printable：把 =XX 还原，软换行去掉
  if (/content-transfer-encoding:\s*quoted-printable/i.test(raw)) {
    body = body.replace(/=\r?\n/g, "").replace(/=([0-9A-Fa-f]{2})/g,
      (_, h) => String.fromCharCode(parseInt(h, 16)));
  }
  // base64 正文
  else if (/content-transfer-encoding:\s*base64/i.test(raw)) {
    try {
      const cleaned = body.replace(/\s+/g, "");
      const bin = atob(cleaned);
      // latin1 → utf8
      body = decodeURIComponent(escape(bin));
    } catch (_) { /* 解不开就保留原文 */ }
  }

  // html 里常见 &amp; 之类，还原一下，方便脚本正则抓链接
  return body
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

export default {
  /** 收信 */
  async email(message, env, ctx) {
    const to = (message.to || "").toLowerCase();
    const raw = await new Response(message.raw).text();
    const subject = message.headers.get("subject") || "";
    const key = `m:${to}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;

    const record = {
      to,
      from: message.from || "",
      subject,
      receivedAt: new Date().toISOString(),
      body: extractBody(raw),
      raw: raw.slice(0, 100_000), // 原文留一份，排障用（截断防止爆 KV 单值上限）
    };

    // 维护 per-address 索引（避免 /inbox 用 list() —— 免费版 list 额度易爆）
    const idxKey = `idx:${to}`;
    let ids = [];
    try {
      const prev = await env.MAIL.get(idxKey);
      if (prev) ids = JSON.parse(prev);
    } catch (_) {}
    ids.unshift(key);
    ids = ids.slice(0, 50); // 每地址最多索引 50 封
    const putIdx = env.MAIL.put(idxKey, JSON.stringify(ids), { expirationTtl: TTL });

    const put = env.MAIL.put(key, JSON.stringify(record), { expirationTtl: TTL });

    // Email Worker 里异步写 KV 有被掐的风险，等它落盘
    const done = Promise.all([put, putIdx]);
    if (ctx && ctx.waitUntil) {
      ctx.waitUntil(done);
    } else {
      await done;
    }
  },

  /** 读信接口 */
  async fetch(req, env) {
    const url = new URL(req.url);

    if (url.pathname === "/health") return json({ ok: true });

    if (!authed(req, env)) return json({ error: "unauthorized" }, 401);

    const to = (url.searchParams.get("to") || "").toLowerCase();

    // 取单封
    if (url.pathname === "/message") {
      const id = url.searchParams.get("id");
      if (!id) return json({ error: "id required" }, 400);
      const v = await env.MAIL.get(id);
      return v ? json({ ok: true, message: JSON.parse(v) })
               : json({ error: "not found" }, 404);
    }

    // 清空某地址
    if (req.method === "DELETE" && url.pathname === "/inbox") {
      if (!to) return json({ error: "to required" }, 400);
      let cursor, n = 0;
      do {
        const page = await env.MAIL.list({ prefix: `m:${to}:`, cursor });
        await Promise.all(page.keys.map((k) => env.MAIL.delete(k.name)));
        n += page.keys.length;
        cursor = page.list_complete ? null : page.cursor;
      } while (cursor);
      return json({ ok: true, deleted: n });
    }

    // 列信（走 idx:<to> 索引，不用 list()）
    if (url.pathname === "/inbox") {
      if (!to) return json({ error: "to required" }, 400);
      const grep = url.searchParams.get("grep") || "";
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "20", 10), 100);

      let keys = [];
      try {
        const idx = await env.MAIL.get(`idx:${to}`);
        if (idx) keys = JSON.parse(idx);
      } catch (_) {}
      // 兼容旧数据: 索引为空时退回 list()（限额恢复后可用）
      if (!keys.length) {
        try {
          const page = await env.MAIL.list({ prefix: `m:${to}:`, limit: 1000 });
          keys = page.keys.map((k) => k.name).sort().reverse();
        } catch (_) {}
      }

      const out = [];
      for (const k of keys) {
        if (out.length >= limit) break;
        const v = await env.MAIL.get(k);
        if (!v) continue;
        const m = JSON.parse(v);
        if (grep && !(m.body || "").includes(grep)) continue;
        // 列表不返回 raw，省流量
        delete m.raw;
        m.id = k;
        out.push(m);
      }
      return json({ ok: true, count: out.length, messages: out });
    }

    return json({ error: "not found" }, 404);
  },
};
