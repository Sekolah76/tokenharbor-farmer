#!/usr/bin/env python3
"""progress.py — 轻量进度条：TTY 原地刷新；非 TTY 按百分比打行；同时落 jsonl 便于 tail -f"""
import json, os, sys, time, threading

class Progress:
    def __init__(self, total, label="", every_pct=5.0, log_path=None, width=28):
        self.total = max(1, int(total))
        self.label = label
        self.every_pct = every_pct
        self.width = width
        self.done = 0
        self.ok = 0
        self.fail = 0
        self.t0 = time.time()
        self.tty = sys.stdout.isatty()
        self.log_path = log_path
        self._last_pct = -1
        self._lock = threading.Lock()
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            self._fh = open(log_path, "a", encoding="utf-8")
            self._fh.write(json.dumps({"event": "start", "label": label, "total": self.total,
                                       "ts": time.strftime("%H:%M:%S")}, ensure_ascii=False) + "\n")
            self._fh.flush()
        else:
            self._fh = None
        self.render(force=True)

    def render(self, force=False):
        pct = self.done * 100.0 / self.total
        el = max(1e-6, time.time() - self.t0)
        rate = self.done / el
        eta = (self.total - self.done) / rate if rate > 0 else 0
        if self.tty:
            filled = int(self.width * self.done / self.total)
            bar = "█" * filled + "·" * (self.width - filled)
            sys.stdout.write(f"\r{self.label} [{bar}] {pct:5.1f}% {self.done}/{self.total} "
                             f"ok={self.ok} fail={self.fail} {rate:4.1f}/s ETA {eta:4.0f}s ")
            sys.stdout.flush()
        else:
            if force or pct >= self._last_pct + self.every_pct or self.done >= self.total:
                self._last_pct = pct
                print(f"{self.label} {pct:5.1f}% {self.done}/{self.total} ok={self.ok} fail={self.fail} "
                      f"{rate:4.1f}/s ETA {eta:4.0f}s", flush=True)

    def tick(self, ok=True, **extra):
        with self._lock:
            self.done += 1
            self.ok += 1 if ok else 0
            self.fail += 0 if ok else 1
            if self._fh:
                self._fh.write(json.dumps({"done": self.done, "ok": self.ok, "fail": self.fail,
                                           "pct": round(self.done * 100.0 / self.total, 1),
                                           "ts": time.strftime("%H:%M:%S"), **extra},
                                          ensure_ascii=False) + "\n")
                self._fh.flush()
            self.render()

    def note(self, text):
        if self.tty:
            sys.stdout.write("\r" + " " * 110 + "\r")
        print(text, flush=True)

    def done_(self, **extra):
        el = time.time() - self.t0
        if self.tty:
            sys.stdout.write("\n")
        msg = f"{self.label} 完成 {self.done}/{self.total} ok={self.ok} fail={self.fail} 用时 {el:.0f}s"
        print(msg, flush=True)
        if self._fh:
            self._fh.write(json.dumps({"event": "done", "elapsed_s": round(el, 1),
                                       "ok": self.ok, "fail": self.fail, **extra},
                                      ensure_ascii=False) + "\n")
            self._fh.close()
