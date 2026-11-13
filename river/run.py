#!/usr/bin/env python3
"""river/run.py — CLI 入口

用法：
    # 注册一个账号并创建 API Key
    python3 river/run.py single

    # 批量 10 个，间隔 8 秒
    python3 river/run.py loop 10 --delay 8

    # 指定邮箱（默认走 th_auto_register.gen_email 临时邮箱）
    python3 river/run.py single --email me@example.com

    # 手机验证策略：skip（默认）| abort | solve
    RIVER_PHONE_VERIFY=abort python3 river/run.py single

    # 只探测认证配置，不注册
    python3 river/run.py probe
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from river.client import fetch_config, log, new_session  # noqa: E402
from river.register import register, register_loop  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.jsonl")


def get_email(args):
    if args.email:
        return args.email
    try:
        import th_auto_register as tar
        tar.SOCKS = {}          # 直连，不走 Tor
        addr, _tok = tar.gen_email()
        log(f"临时邮箱：{addr}")
        return addr
    except Exception as e:
        raise SystemExit(f"没有 --email 且临时邮箱不可用：{e}\n"
                         f"请直接传 --email you@example.com")


def cmd_probe(args):
    s = new_session()
    cfg = fetch_config(s)
    print(json.dumps(cfg, indent=2))
    print(f"\n注册通道：{'短路径 /api/auth/signup' if cfg.get('password_signup') else '长路径 WorkOS AuthKit'}")
    print(f"手机短信通道：{'启用' if cfg.get('sms') or cfg.get('phone') else '未启用（River 当前无短信验证）'}")


def cmd_single(args):
    email = get_email(args)
    r = register(email=email, team_id=args.team, mode=args.mode,
                 wait_propagation=not args.no_wait, timeout=args.timeout,
                 first_name=args.first_name, last_name=args.last_name,
                 headless=not args.headed, proxy=args.proxy,
                 human_pace=args.human_pace)
    print("\n=== RESULT ===")
    for k, v in r.items():
        if k in ("access_token", "refresh_token"):
            v = (v[:24] + "...") if v else ""
        print(f"  {k}: {v}")
    if args.out:
        with open(args.out, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n已写入 {args.out}")
    return r


def cmd_loop(args):
    if args.email:
        r = register(email=args.email, team_id=args.team, mode=args.mode,
                     timeout=args.timeout, headless=not args.headed,
                     proxy=args.proxy, human_pace=args.human_pace)
        print(json.dumps({k: (v[:24] + "..." if k in ("access_token", "refresh_token") and v else v)
                          for k, v in r.items()}, indent=2, ensure_ascii=False))
        return [r]
    rs = register_loop(args.n, delay=args.delay, team_id=args.team, out=args.out,
                       mode=args.mode, timeout=args.timeout,
                       headless=not args.headed, proxy=args.proxy,
                       human_pace=args.human_pace, max_retry=args.max_retry,
                       proxies_list=([p.strip() for p in args.proxies.split(",")
                                     if p.strip()] if args.proxies else None))
    ok = sum(1 for r in rs if r.get("ok") and r.get("api_key"))
    print(f"\n完成 {len(rs)} 个，成功拿 key {ok} 个 → {args.out}")
    return rs


def cmd_capture(args):
    """抓取 AuthKit 注册表单的真实提交协议（研究用）。"""
    os.system(f"{sys.executable} {os.path.join(os.path.dirname(__file__), 'capture_signup.py')}")
    return None


def main():
    ap = argparse.ArgumentParser("river — river.ai 注册 + 拿 API Key")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="只看认证配置")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("capture", help="抓取注册表单真实提交协议（CDP）")
    p.set_defaults(fn=cmd_capture)

    p = sub.add_parser("single", help="注册一个")
    p.add_argument("--email")
    p.add_argument("--team", help="X-River-Team，团队账号才需要")
    p.add_argument("--mode", choices=["browser", "http"], default="browser",
                   help="browser=真实 Chrome（默认）；http=纯 HTTP（常被 Radar 拦）")
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--first-name", default="Test")
    p.add_argument("--last-name", default="User")
    p.add_argument("--headed", action="store_true",
                   help="有界面模式（去掉 headless），对抗 Radar 更有效")
    p.add_argument("--proxy", help="host:port 或 socks5://host:port，给浏览器挂代理")
    p.add_argument("--human-pace", action="store_true",
                   help="字段间加随机延时，降低行为时序特征")
    p.add_argument("--no-wait", action="store_true", help="跳过传播等待")
    p.add_argument("--out", default=OUT)
    p.set_defaults(fn=cmd_single)

    p = sub.add_parser("loop", help="批量注册")
    p.add_argument("n", type=int, nargs="?", default=5)
    p.add_argument("--delay", type=int, default=8)
    p.add_argument("--email", help="给了就只跑这一个")
    p.add_argument("--team")
    p.add_argument("--mode", choices=["browser", "http"], default="browser")
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--proxy")
    p.add_argument("--human-pace", action="store_true")
    p.add_argument("--max-retry", type=int, default=3,
                   help="每个账号最多试几次（风控拦截时换出口重试）")
    p.add_argument("--proxies", help="逗号分隔的代理列表，每轮换一个，如 "
                                     "socks5://127.0.0.1:9050,http://127.0.0.1:7890")
    p.add_argument("--out", default=OUT)
    p.set_defaults(fn=cmd_loop)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()