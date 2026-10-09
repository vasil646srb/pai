"""MCP client: config validation, stdio round-trip, permissions, owner API."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from pai.mcp_client import (
    McpManager, McpServerConfig, McpServerRegistry, exposed_tool_name, public_view,
    validate_server_config,
)

STUB = str(Path(__file__).with_name("mcp_stub_server.py"))


def _stdio(name="stub", **over):
    body = dict(name=name, command=sys.executable, args=[STUB], timeout_seconds=20)
    body.update(over)
    return McpServerConfig(**body)


@pytest.fixture
def manager():
    m = McpManager(McpServerRegistry(Path(tempfile.mkdtemp()) / "mcp.json"))
    yield m
    m.stop()


def test_validation():
    validate_server_config(_stdio())
    validate_server_config(McpServerConfig(name="remote", transport="http", url="https://x.example/mcp"))
    bad = [
        _stdio(name="Bad-Name"),
        _stdio(name="a" * 30),
        _stdio(command=""),
        _stdio(url="https://x"),
        McpServerConfig(name="r", transport="http", url="ftp://x"),
        McpServerConfig(name="r", transport="http", url="https://x", command="python"),
        McpServerConfig(name="r", transport="websocket", url="https://x"),
        _stdio(env={"A": 1}),
    ]
    for cfg in bad:
        with pytest.raises(ValueError):
            validate_server_config(cfg)


def test_timeout_clamped_and_tool_name_bounded():
    cfg = validate_server_config(_stdio(timeout_seconds=10_000))
    assert cfg.timeout_seconds == 300.0
    long = exposed_tool_name("stub", "x" * 200)
    assert len(long) <= 64 and long.startswith("mcp__stub__")
    assert exposed_tool_name("stub", "we ird.name") == "mcp__stub__we_ird_name"


def test_registry_persists_and_masks_secrets():
    path = Path(tempfile.mkdtemp()) / "mcp.json"
    reg = McpServerRegistry(path)
    cfg = reg.add(_stdio(env={"TOKEN": "s3cret"}))
    assert public_view(cfg)["env"] == {"TOKEN": "***"}
    with pytest.raises(ValueError):
        reg.add(_stdio(name="stub"))                       # duplicate name
    reloaded = McpServerRegistry(path)
    assert reloaded.get(cfg.id).env == {"TOKEN": "s3cret"}


def test_stdio_roundtrip_and_tool_errors(manager):
    cfg = manager.registry.add(_stdio())
    st = manager.reload(cfg.id)
    assert st["status"] == "ready", st
    assert {"mcp__stub__echo", "mcp__stub__add", "mcp__stub__boom"} <= manager.tool_names()
    schema = next(s for s in manager.schemas() if s["function"]["name"] == "mcp__stub__add")
    assert schema["function"]["parameters"]["properties"]["a"]
    assert manager.call("mcp__stub__echo", {"text": "hi"})["output"] == "hi"
    assert manager.call("mcp__stub__add", {"a": 2, "b": 3})["structured"] == {"result": 5}
    failed = manager.call("mcp__stub__boom", {})
    assert failed["tool_failed"] and failed["ok"] is False


def test_failed_start_reports_error_without_hanging(manager):
    cfg = manager.registry.add(_stdio(name="broken", command=sys.executable, args=["-c", "import sys; sys.exit(3)"],
                                      timeout_seconds=5))
    t = time.time()
    st = manager.reload(cfg.id)
    assert st["status"] == "error" and st["error"]
    assert time.time() - t < 15
    with pytest.raises(Exception):
        manager.call("mcp__broken__echo", {})


def test_call_timeout_is_reported(manager):
    cfg = manager.registry.add(_stdio(timeout_seconds=2))
    assert manager.reload(cfg.id)["status"] == "ready"
    with pytest.raises(Exception, match="timed out"):
        manager.call("mcp__stub__slow", {})


def test_disable_and_remove_disconnect(manager):
    cfg = manager.registry.add(_stdio())
    manager.reload(cfg.id)
    manager.registry.update(cfg.id, {"enabled": False})
    assert manager.reload(cfg.id)["status"] == "disabled"
    assert not manager.has_tool("mcp__stub__echo")
    manager.registry.update(cfg.id, {"enabled": True})
    assert manager.reload(cfg.id)["status"] == "ready"
    manager.remove(cfg.id)
    assert manager.registry.get(cfg.id) is None and not manager.tool_names()


def test_masked_secrets_are_kept_on_update():
    reg = McpServerRegistry()
    cfg = reg.add(_stdio(env={"TOKEN": "s3cret", "A": "1"}))
    reg.update(cfg.id, {"env": {"TOKEN": "***", "A": "2"}, "timeout_seconds": 12})
    assert reg.get(cfg.id).env == {"TOKEN": "s3cret", "A": "2"}
    assert reg.get(cfg.id).timeout_seconds == 12
