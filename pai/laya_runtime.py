"""Laya runtime: the project finds, installs and runs the local Laya API server by itself.

ensure() order:
  1. something already answers on the Laya port        -> use it (we start nothing);
  2. a `laya-system-one` CLI is on disk / PATH          -> start it (node cli.js, bound to 127.0.0.1);
  3. nothing installed                                  -> `npm install` (pinned, --ignore-scripts) -> start it.
A watchdog thread then keeps it alive (restart with backoff). Everything runs off the main thread, so app startup
never waits on npm or on the model load; until the server is up `Laya.ready` is False and gates fall back to the
old behaviour. Only a process this runtime started is ever stopped.

Env: PAI_LAYA_AUTOSTART=1, PAI_LAYA_AUTOINSTALL=1, PAI_LAYA_VERSION=1.3.3, PAI_LAYA_DIR (install dir, default
<data_dir>/laya), PAI_LAYA_CLI (explicit path to bin/cli.js). No API key: the server binds to 127.0.0.1 only.
Autostart applies only to loopback URLs.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .laya import Laya

_log = logging.getLogger(__name__)

_PROBE = {"state": "ping", "questions": {"p": {"type": "choice", "instructions": "Pick one.", "criteria": ["a", "b"]}}}
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _is_timeout(e: Exception) -> bool:
    reason = getattr(e, "reason", e)
    return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(e).lower()


class LayaRuntime:
    def __init__(self, laya: Laya, data_dir: str, *, autostart: bool = True, autoinstall: bool = True,
                 version: str = "1.3.3", install_dir: str = "", cli_path: str = "", node: str = "node",
                 npm: str = "npm", start_timeout: float = 300.0, install_timeout: float = 900.0,
                 poll: float = 30.0, probe_timeout: float = 60.0, backend: str = ""):
        self.laya = laya
        self.data_dir = Path(data_dir)
        u = urlparse(laya.url or "http://127.0.0.1:8090")
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 8090
        self.autostart = autostart and self.host in _LOOPBACK
        self.autoinstall, self.version = autoinstall, version
        self.install_dir = Path(install_dir) if install_dir else self.data_dir / "laya"
        self.cli_path, self.node, self.npm = cli_path, node, npm
        self.start_timeout, self.install_timeout, self.poll = start_timeout, install_timeout, poll
        self.probe_timeout = probe_timeout  # a slow (wasm) server may need a while for its first answer
        self.backend = backend              # "" = package default (native); "wasm" on Termux
        self.state, self.error = "idle", ""
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, laya: Laya, data_dir: str, env=os.environ) -> "LayaRuntime":
        return cls(laya, data_dir,
                   autostart=env.get("PAI_LAYA_AUTOSTART", "1") != "0",
                   autoinstall=env.get("PAI_LAYA_AUTOINSTALL", "1") != "0",
                   version=env.get("PAI_LAYA_VERSION", "1.3.3"),
                   install_dir=env.get("PAI_LAYA_DIR", ""), cli_path=env.get("PAI_LAYA_CLI", ""),
                   poll=float(env.get("PAI_LAYA_POLL", "30")),
                   probe_timeout=float(env.get("PAI_LAYA_PROBE_TIMEOUT", "60")),
                   backend=env.get("PAI_LAYA_BACKEND", ""))

    # ---------- public ----------
    def start(self) -> None:
        if not self.laya.enabled or self._thread:
            return
        if getattr(self.laya, "remote", False):  # cloud provider: nothing to start, nothing to watch
            self.laya.ready, self.state = True, "remote"
            return
        self.laya.ready = False
        self._thread = threading.Thread(target=self._loop, name="pai-laya", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass

    def status(self) -> dict:
        with self.laya._lock:
            stats = dict(self.laya.stats)
        return {"mode": self.laya.mode, "state": self.state, "ready": self.laya.ready, "error": self.error,
                "url": self.laya.url, "pid": self._proc.pid if self._proc and self._proc.poll() is None else None,
                "autostart": self.autostart, "breaker_open": time.monotonic() < self.laya._open_until,
                "timeout": self.laya.timeout, "stats": stats}

    # ---------- internals ----------
    def _tcp_up(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1.5):
                return True
        except OSError:
            return False

    def _probe(self) -> str:
        """'ok' | 'busy' (no answer within probe_timeout: a slow Laya, not a foreign one) |
        'foreign' (answers, but not Laya) | 'down' (port closed)."""
        if not self._tcp_up():
            return "down"
        try:
            req = urllib.request.Request(
                f"{self.laya.url}/v1/systemone", data=json.dumps(_PROBE).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.probe_timeout) as r:
                data = json.loads(r.read(100_000).decode("utf-8", "replace"))
            return "ok" if isinstance(data, dict) and "answers" in data else "foreign"
        except Exception as e:
            return "busy" if _is_timeout(e) else "foreign"

    def _find_cli(self) -> str:
        cands = [self.cli_path, str(self.install_dir / "node_modules/laya-system-one/bin/cli.js"),
                 str(Path.home() / "laya-api/node_modules/laya-system-one/bin/cli.js")]
        for c in cands:
            if c and Path(c).is_file():
                return c
        return ""

    def _fix_exec_bits(self) -> None:
        scope = self.install_dir / "node_modules/@sys-one"
        if not scope.is_dir():
            return
        for d in scope.glob("laya-serve*/bin/*/*"):
            if d.is_file() and not d.name.endswith(".exe"):
                try:
                    d.chmod(0o755)
                except OSError:
                    pass

    def _install(self) -> None:
        if not shutil.which(self.npm):
            raise RuntimeError("npm не найден (Termux: pkg install nodejs-lts)")
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.state = "installing"
        _log.info("laya: npm install laya-system-one@%s -> %s", self.version, self.install_dir)
        r = subprocess.run([self.npm, "install", "--prefix", str(self.install_dir), "--ignore-scripts",
                            "--no-audit", "--no-fund", f"laya-system-one@{self.version}"],
                           capture_output=True, text=True, timeout=self.install_timeout)
        if r.returncode != 0:
            raise RuntimeError(f"npm install failed: {(r.stderr or r.stdout)[-300:]}")
        self._fix_exec_bits()

    def _spawn(self, cli: str) -> None:
        if not shutil.which(self.node):
            raise RuntimeError("node не найден (Termux: pkg install nodejs-lts)")
        self.state = "starting"
        self._fix_exec_bits()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.data_dir / "laya.log", "ab")
        self._proc = subprocess.Popen(
            [self.node, cli, "--host", "127.0.0.1", "--port", str(self.port)]
            + (["--backend", self.backend] if self.backend else []),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(Path(cli).parents[1]),
            start_new_session=True)
        deadline = time.time() + self.start_timeout  # first start assembles the ~324 MB model: be patient
        while time.time() < deadline and not self._stop.is_set():
            if self._proc.poll() is not None:
                raise RuntimeError(f"laya завершился, код {self._proc.returncode} (см. {self.data_dir / 'laya.log'})")
            if self._probe() == "ok":
                return
            time.sleep(1)
        raise RuntimeError("laya не поднялся за отведённое время")

    def _ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.data_dir / "laya.lock", "w") as lock:  # several workers: one starts, others wait and probe
            fcntl.flock(lock, fcntl.LOCK_EX)
            p = self._probe()
            if p in ("ok", "busy"):  # busy = something slow holds the port: never start a second server on it
                return
            if p == "foreign":
                raise RuntimeError("порт %d занят не Laya: задайте другой PAI_LAYA_URL" % self.port)
            if not self.autostart:
                raise RuntimeError("сервер Laya не отвечает, автозапуск выключен")
            cli = self._find_cli()
            if not cli:
                if not self.autoinstall:
                    raise RuntimeError("laya-system-one не установлен, автоустановка выключена")
                self._install()
                cli = self._find_cli()
                if not cli:
                    raise RuntimeError("после установки cli.js не найден")
            self._spawn(cli)

    def _loop(self) -> None:
        fails = 0
        while not self._stop.is_set():
            try:
                if not self._tcp_up():
                    self.laya.ready = False
                    self.state = "down"
                    self._ensure()
                probe = self._probe()
                if probe == "ok":
                    if not self.laya.ready:
                        _log.info("laya: ready at %s", self.laya.url)
                    self.laya.ready, self.state, self.error, fails = True, "ready", "", 0
                elif probe == "busy":
                    # keep the current `ready` flag: a slow answer is not a failure, no flapping
                    self.state, self.error, fails = "busy", f"нет ответа за {self.probe_timeout:g} с", 0
                else:
                    raise RuntimeError(f"порт {self.port} занят не Laya: задайте другой PAI_LAYA_URL")
            except Exception as e:  # never kill the thread; back off and retry
                fails += 1
                self.laya.ready, self.state, self.error = False, "failed", str(e)[:300]
                _log.warning("laya: %s (retry in %ss)", e, min(300, 5 * 2 ** fails))
                self._stop.wait(min(300, 5 * 2 ** fails))
                continue
            self._stop.wait(self.poll)
