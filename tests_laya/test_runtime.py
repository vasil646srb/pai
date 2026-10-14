import os, socket, stat, sys, textwrap, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from pai.laya import Laya
from pai.laya_runtime import LayaRuntime

FAKE_CLI = textwrap.dedent("""
    const http = require('http');
    const a = process.argv; const port = +a[a.indexOf('--port') + 1]; const host = a[a.indexOf('--host') + 1];
    http.createServer((req, res) => {
      let b = ''; req.on('data', d => b += d); req.on('end', () => {
        const q = JSON.parse(b).questions, out = {};
        for (const n in q) out[n] = {type:'choice', choice:q[n].criteria[0], probabilities:{[q[n].criteria[0]]:0.9}, confidence:0.5};
        res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify({model:'fake', answers: out}));
      });
    }).listen(port, host);
""")

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def mk_cli(d):
    f = d / "node_modules/laya-system-one/bin/cli.js"; f.parent.mkdir(parents=True); f.write_text(FAKE_CLI); return f

def wait(rt, state, t=20):
    end = time.time() + t
    while time.time() < end and rt.state != state: time.sleep(0.1)
    return rt.state == state

def rt_for(tmp, port, **kw):
    laya = Laya(f"http://127.0.0.1:{port}", "on")
    return LayaRuntime(laya, str(tmp / "data"), install_dir=str(tmp / "inst"), poll=0.3, start_timeout=15, **kw)

def test_starts_installed_cli_and_stops(tmp_path):
    port = free_port(); mk_cli(tmp_path / "inst")
    rt = rt_for(tmp_path, port); rt.start()
    try:
        assert wait(rt, "ready") and rt.laya.ready and rt.status()["pid"]
        assert "--api-key" not in " ".join(rt._proc.args)
        v = rt.laya.choose("hello", "i", ["x", "y"]); assert v and v.label == "x"   
    finally:
        rt.stop()
    time.sleep(0.5); assert not rt._tcp_up()

def test_uses_running_server_without_spawning(tmp_path):
    port = free_port(); mk_cli(tmp_path / "ext")
    import subprocess; p = subprocess.Popen(["node", str(tmp_path / "ext/node_modules/laya-system-one/bin/cli.js"), "--host", "127.0.0.1", "--port", str(port)])
    try:
        time.sleep(1)
        rt = rt_for(tmp_path, port); rt.start()
        assert wait(rt, "ready") and rt.status()["pid"] is None   # ничего не запускали
        rt.stop(); assert rt._tcp_up()                            # чужой сервер не трогаем
    finally:
        p.terminate()

def test_foreign_server_is_reported_not_killed(tmp_path):
    import http.server, threading
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    rt = rt_for(tmp_path, srv.server_port); rt.start()
    try:
        assert wait(rt, "failed") and "не Laya" in rt.error and not rt.laya.ready and rt.status()["pid"] is None
    finally:
        rt.stop(); srv.shutdown()

def test_installs_when_missing(tmp_path):
    port = free_port(); fake_npm = tmp_path / "npm"
    src = tmp_path / "fake_cli.js"; src.write_text(FAKE_CLI)
    fake_npm.write_text(f'#!/bin/sh\nprefix=""\nwhile [ $# -gt 0 ]; do [ "$1" = "--prefix" ] && prefix="$2"; shift; done\n'
                        f'mkdir -p "$prefix/node_modules/laya-system-one/bin" && cp {src} "$prefix/node_modules/laya-system-one/bin/cli.js"\n')
    fake_npm.chmod(0o755)
    rt = rt_for(tmp_path, port, npm=str(fake_npm)); rt.start()
    try:
        assert wait(rt, "ready")
    finally:
        rt.stop()

def test_no_cli_no_autoinstall_fails_cleanly(tmp_path):
    rt = rt_for(tmp_path, free_port(), autoinstall=False); rt._find_cli = lambda: ""; rt.start()
    try:
        assert wait(rt, "failed") and "автоустановка" in rt.error and not rt.laya.ready
    finally:
        rt.stop()

def test_remote_url_never_autostarts(tmp_path):
    laya = Laya("http://10.0.0.5:8090", "on"); rt = LayaRuntime(laya, str(tmp_path))
    assert rt.autostart is False

def test_watchdog_restarts_killed_server(tmp_path):
    import signal
    port = free_port(); mk_cli(tmp_path / "inst")
    rt = rt_for(tmp_path, port); rt.start()
    try:
        assert wait(rt, "ready"); old = rt._proc.pid
        os.killpg(old, signal.SIGKILL)
        end = time.time() + 30
        while time.time() < end and not (rt.laya.ready and rt._proc.pid != old): time.sleep(0.2)
        assert rt.laya.ready and rt._proc.pid != old
    finally:
        rt.stop()


def test_slow_server_is_busy_not_foreign(tmp_path):
    import http.server, threading
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(3)  # like a wasm server on its first answer
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rt = rt_for(tmp_path, srv.server_port); rt.probe_timeout = 0.5; rt.poll = 0.2
    rt.start()
    try:
        assert wait(rt, "busy")
        assert rt.status()["pid"] is None and "не Laya" not in rt.error   # ничего не запускали, не объявили чужим
    finally:
        rt.stop(); srv.shutdown()

def test_backend_flag_is_passed(tmp_path):
    port = free_port(); cli = mk_cli(tmp_path / "inst")
    rt = rt_for(tmp_path, port, backend="wasm"); rt.start()
    try:
        assert wait(rt, "ready") and "--backend" in rt._proc.args and "wasm" in rt._proc.args
    finally:
        rt.stop()
