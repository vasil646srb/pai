"""Local command execution tool."""

from __future__ import annotations

import os
import subprocess

from ..settings import termux_bash_path
from .errors import ToolError


def _tool_shell_exec_impl(args):
    runtime = str(args.get("runtime", "bash")).lower()
    command = str(args.get("command", ""))
    if not command.strip():
        raise ToolError("shell_exec requires a non-empty command")
    timeout = max(1, min(int(args.get("timeout", 30)), 300))
    max_output = max(1024, min(int(args.get("max_output_chars", 50_000)), 500_000))
    cwd = str(args.get("cwd") or os.getcwd())

    if runtime == "bash":
        argv = ["bash", "-lc", command]
    elif runtime == "termux":
        # Prefer the Termux bash if available; otherwise use the normal shell
        # so the tool also works when the server itself is running in Termux.
        termux_bash = termux_bash_path()
        if termux_bash and os.path.exists(termux_bash):
            argv = [termux_bash, "-lc", command]
        else:
            argv = ["bash", "-lc", command]
    elif runtime == "python3":
        argv = ["python3", "-c", command]
    else:
        raise ToolError("runtime must be one of: bash, termux, python3")

    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except FileNotFoundError as e:
        raise ToolError(f"Runtime not found: {argv[0]}") from e
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        partial = out[-max_output:]
        raise ToolError(f"Command timed out after {timeout}s. Partial output:\n{partial}") from e
    except OSError as e:
        raise ToolError(f"Failed to execute command: {e}") from e

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + ("\n[stderr]\n" + stderr if stderr else "")
    truncated = len(combined) > max_output
    if truncated:
        combined = combined[-max_output:]
    return {
        "runtime": runtime,
        "command": command,
        "cwd": cwd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "output": combined,
        "truncated": truncated,
    }


class ShellToolsMixin:
    """Local command execution tool."""

    def _tool_shell_exec(self, args, persona):
        try:
            result = _tool_shell_exec_impl(args)
        except ToolError as e:
            return {"tool_failed": True, "error": str(e), "tool": "shell_exec"}
        return result
