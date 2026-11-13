#!/usr/bin/env python3
"""duckip_relay.py — 本地 TCP 转发：127.0.0.1:18080 → (经 mihomo SOCKS5) → proxy.duckip.net:1000
让 duckip 看到的客户端 IP 是机场出口（已白名单），而不是被禁的中国大陆家宽 IP。"""
import socket, threading, socks, sys

LISTEN = ("127.0.0.1", 18080)
TARGET = ("proxy.duckip.net", 1000)
SOCKS = ("127.0.0.1", 7890)

def pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d: break
            b.sendall(d)
    except Exception:
        pass
    finally:
        try: b.shutdown(socket.SHUT_WR)
        except Exception: pass

def handle(cli):
    try:
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, *SOCKS)
        s.settimeout(30)
        s.connect(TARGET)
        t = threading.Thread(target=pump, args=(cli, s), daemon=True)
        t.start()
        pump(s, cli)
        t.join(timeout=5)
    except Exception:
        pass
    finally:
        try: cli.close()
        except Exception: pass
        try: s.close()
        except Exception: pass

def main():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(200)
    print(f"relay listening {LISTEN[0]}:{LISTEN[1]} → {TARGET[0]}:{TARGET[1]} via SOCKS5 {SOCKS[0]}:{SOCKS[1]}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

if __name__ == "__main__":
    main()
