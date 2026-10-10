"""User-confirmed, model-proposed custom functions.

Functions may be LLM-backed, HTTP-backed, or explicit Python functions.
Python functions are never executed while a proposal is created: execution is
available only after the owner validates and activates the function. Python is
run in a separate subprocess with a timeout and output limit.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import new_id, now_ts


@dataclass
class CustomFunction:
    id: str = field(default_factory=new_id)
    name: str = "custom_function"
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    kind: str = "llm_prompt"  # llm_prompt | http_json | dynamic | python
    implementation: dict[str, Any] = field(default_factory=dict)
    status: str = "draft"  # draft | validated | active | rejected | archived
    created_at: float = field(default_factory=now_ts)
    validated_at: float | None = None
    activated_at: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> CustomFunction:
        """Load a function from persisted JSON, including legacy JSON-string fields.

        Older versions could persist ``implementation`` (and sometimes
        ``parameters``) as a JSON-encoded string.  Pydantic expects these
        fields to be dictionaries, so normalize them at the storage boundary.
        """
        data = dict(d)

        for field_name in ("implementation", "parameters"):
            value = data.get(field_name)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    value = {}
            if not isinstance(value, dict):
                value = {}
            data[field_name] = value

        return CustomFunction(**data)


class CustomFunctionRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._functions: dict[str, CustomFunction] = {}
        if self.path and self.path.exists():
            self.load()

    def all(self) -> list[CustomFunction]:
        return list(self._functions.values())

    def get(self, function_id: str) -> CustomFunction | None:
        return self._functions.get(function_id)

    def add(self, fn: CustomFunction, *, validate: bool = True) -> CustomFunction:
        # On normal creation, a function must pass validation.
        # For an LLM proposal, we allow saving a draft even with an
        # unsupported implementation; the check runs before validate/activate.
        if validate:
            self._validate(fn)
        # Normalize the JSON Schema even for a draft, because the LLM may
        # send OBJECT/STRING/NUMBER types in uppercase.
        if not isinstance(fn.parameters, dict):
            raise ValueError("Function parameters must be a JSON Schema object")
        fn.parameters = self._normalize_json_schema(fn.parameters)
        reserved = {"get_fragment", "search_archive", "calculate_time_delta", "list_fragments_by_timeframe", "web_search", "web_fetch", "propose_function", "propose_rule"}
        if fn.name in reserved:
            raise ValueError(f"Function name is reserved: {fn.name}")
        if any(x.name == fn.name and x.id != fn.id for x in self._functions.values()):
            raise ValueError(f"Function name already exists: {fn.name}")
        self._functions[fn.id] = fn
        self.save()
        return fn

    def validate(self, function_id: str) -> CustomFunction:
        fn = self._require(function_id)
        if fn.status != "draft":
            raise ValueError(f"Cannot validate function from status {fn.status}")
        self._validate(fn)
        fn.status = "validated"
        fn.validated_at = now_ts()
        self.save()
        return fn

    def activate(self, function_id: str) -> CustomFunction:
        fn = self._require(function_id)
        if fn.status != "validated":
            raise ValueError("Function must be validated before activation")
        self._validate(fn)
        fn.status = "active"
        fn.activated_at = now_ts()
        self.save()
        return fn

    def archive(self, function_id: str) -> CustomFunction:
        fn = self._require(function_id)
        if fn.status != "active":
            raise ValueError(f"Only active functions can be archived; current status is {fn.status}")
        fn.status = "archived"
        self.save()
        return fn

    def reject(self, function_id: str) -> CustomFunction:
        fn = self._require(function_id)
        if fn.status == "active":
            raise ValueError("Active functions cannot be rejected; archive them first")
        fn.status = "rejected"
        self.save()
        return fn

    def remove(self, function_id: str) -> None:
        self._require(function_id)
        self._functions.pop(function_id, None)
        self.save()

    def active(self) -> list[CustomFunction]:
        return [f for f in self._functions.values() if f.status == "active"]

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": f.name,
                    "description": f.description,
                    "parameters": f.parameters,
                },
            }
            for f in self.active()
        ]

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([f.to_dict() for f in self.all()], ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> None:
        if not self.path:
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._functions = {}
        changed = False
        for d in data:
            fn = CustomFunction.from_dict(d)

            # Normalize legacy JSON-string fields and JSON Schema casing.
            # from_dict() already converts legacy strings to dictionaries;
            # comparing the canonical representation lets us persist the
            # migration so the next process start reads clean data directly.
            normalized_parameters = self._normalize_json_schema(fn.parameters)
            if normalized_parameters != fn.parameters:
                fn.parameters = normalized_parameters

            canonical = fn.to_dict()
            if canonical != d:
                changed = True

            self._functions[fn.id] = fn

        if changed:
            self.save()

    def get_by_name(self, name: str) -> CustomFunction | None:
        for fn in self._functions.values():
            if fn.name == name and fn.status == "active":
                return fn
        return None

    def execute(self, name: str, arguments: dict, llm=None) -> dict:
        fn = self.get_by_name(name)
        if not fn:
            raise RuntimeError(f"Unknown or inactive custom function: {name}")
        if fn.kind == "python":
            return self._execute_python(fn, arguments)
        if fn.kind in ("llm_prompt", "dynamic"):
            if llm is None:
                raise RuntimeError("LLM client is required for LLM-backed custom functions")
            if fn.kind == "dynamic" and fn.implementation.get("inline_code"):
                return self._execute_python(fn, arguments)
            if fn.kind == "dynamic":
                sign = str(fn.implementation.get("dynamic_sign", fn.name)).strip()
                prompt = (
                    f"Execute the declarative custom function {sign}.\n"
                    f"Description: {fn.description}\n"
                    f"Input parameters: {{args}}\n"
                    "Return only the function's final result, with no explanations."
                )
            else:
                prompt = str(fn.implementation.get("prompt", ""))
            rendered = prompt.replace("{{args}}", json.dumps(arguments, ensure_ascii=False))
            for key, value in arguments.items():
                value_text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                rendered = rendered.replace("{{" + str(key) + "}}", value_text)
            result = llm.chat(messages=[
                {"role": "system", "content": "Execute the custom function strictly per the instructions. Return only the function's result."},
                {"role": "user", "content": rendered},
            ])
            return {"function": name, "result": result.get("content", "")}
        if fn.kind == "http_json":
            import urllib.request
            from urllib.parse import quote
            url = str(fn.implementation["url"])
            method = str(fn.implementation.get("method", "GET")).upper()
            for key, value in arguments.items():
                url = url.replace("{{" + str(key) + "}}", quote(str(value), safe=""))
            body = None
            if method != "GET":
                body = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read(200_000).decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = raw
                return {"function": name, "status": resp.status, "result": data}
        raise RuntimeError("Unsupported custom function kind")

    @staticmethod
    def _validate_python(fn: CustomFunction) -> None:
        import ast
        code = str(fn.implementation.get("inline_code", ""))
        if not code.strip():
            raise ValueError("python requires implementation.inline_code")
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Python syntax error: {e}") from e
        functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if fn.name not in functions:
            raise ValueError(f"Python implementation must define function '{fn.name}'")

    @staticmethod
    def _execute_python(fn: CustomFunction, arguments: dict) -> dict:
        import base64
        import os
        import subprocess
        import sys
        code = str(fn.implementation.get("inline_code", ""))
        timeout = max(1, min(int(fn.implementation.get("timeout_seconds", 30)), 120))
        max_output = max(1024, min(int(fn.implementation.get("max_output_chars", 200_000)), 1_000_000))
        runner = """import base64,json,sys,traceback,io,contextlib
code=base64.b64decode(sys.stdin.readline().strip()).decode('utf-8')
args=json.loads(sys.stdin.readline())
name=sys.stdin.readline().rstrip(chr(10))
ns={'__name__':'__custom_function__'}
try:
    exec(compile(code,'<custom_function>','exec'),ns,ns)
    fn=ns.get(name)
    if not callable(fn): raise RuntimeError('Function is not callable')
    with contextlib.redirect_stdout(io.StringIO()):
        result=fn(**args)
    try: json.dumps(result,ensure_ascii=False); output=result
    except TypeError: output=repr(result)
    print("@@PAI_RESULT@@" + json.dumps({'ok':True,'result':output},ensure_ascii=False))
except Exception as exc:
    print("@@PAI_RESULT@@" + json.dumps({'ok':False,'error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc()},ensure_ascii=False))
"""
        env={k:v for k,v in os.environ.items() if k in {"PATH","PYTHONPATH","HOME","LANG","LC_ALL","TMPDIR"}}
        payload = base64.b64encode(code.encode("utf-8")).decode("ascii") + chr(10) + json.dumps(arguments, ensure_ascii=False) + chr(10) + fn.name + chr(10)
        try:
            proc=subprocess.run([sys.executable,"-c",runner],input=payload,text=True,capture_output=True,timeout=timeout,cwd=os.getcwd(),env=env)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Python function timed out after {timeout}s") from e
        stdout=proc.stdout[-max_output:]
        if not stdout:
            raise RuntimeError(f"Python function produced no result. stderr: {proc.stderr[-4000:]}")
        marker = "@@PAI_RESULT@@"
        lines = [line for line in stdout.strip().splitlines() if line.startswith(marker)]
        if not lines:
            raise RuntimeError(f"Invalid Python function output: {stdout[-4000:]}")
        try:
            data = json.loads(lines[-1][len(marker):])
        except ValueError as e:
            raise RuntimeError(f"Invalid Python function output: {stdout[-4000:]}") from e
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "Python function failed") + chr(10) + data.get("traceback", "")[-4000:])
        return {"function":fn.name,"result":data.get("result"),"execution":"python_subprocess"}

    def _require(self, function_id: str) -> CustomFunction:
        fn = self.get(function_id)
        if not fn:
            raise KeyError(f"No such function: {function_id}")
        return fn

    @staticmethod
    def _normalize_json_schema(value: Any) -> Any:
        """Normalizes the JSON Schema if the model sent types in uppercase."""
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "type":
                    if isinstance(item, str):
                        result[key] = item.lower()
                    elif isinstance(item, list):
                        result[key] = [x.lower() if isinstance(x, str) else x for x in item]
                    else:
                        result[key] = item
                else:
                    result[key] = CustomFunctionRegistry._normalize_json_schema(item)
            return result
        if isinstance(value, list):
            return [CustomFunctionRegistry._normalize_json_schema(item) for item in value]
        return value

    @staticmethod
    def _validate(fn: CustomFunction) -> None:
        # Models sometimes return a JSON Schema with TYPE/OBJECT/STRING/NUMBER.
        # The canonical JSON Schema form uses lowercase types.
        if not isinstance(fn.parameters, dict):
            raise ValueError("Function parameters must be a JSON Schema object")
        fn.parameters = CustomFunctionRegistry._normalize_json_schema(fn.parameters)
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{1,63}", fn.name):
            raise ValueError("Function name must match [a-zA-Z][a-zA-Z0-9_]{1,63}")
        if fn.name.startswith("__"):
            raise ValueError("Dunder-like function names are not allowed")
        if fn.kind not in ("llm_prompt", "http_json", "dynamic", "python"):
            raise ValueError("Unsupported function kind")
        if fn.parameters.get("type") != "object":
            raise ValueError("Function parameters must be a JSON Schema object")
        if fn.kind == "llm_prompt":
            if not str(fn.implementation.get("prompt", "")).strip():
                raise ValueError("llm_prompt requires implementation.prompt")
        if fn.kind == "dynamic":
            if fn.implementation.get("inline_code"):
                CustomFunctionRegistry._validate_python(fn)
            else:
                sign = str(fn.implementation.get("dynamic_sign", "")).strip()
                if not sign:
                    raise ValueError("dynamic requires implementation.dynamic_sign")
        if fn.kind == "python":
            CustomFunctionRegistry._validate_python(fn)
        if fn.kind == "http_json":
            url = str(fn.implementation.get("url", "")).strip()
            method = str(fn.implementation.get("method", "GET")).upper()
            if not (url.startswith("https://") or url.startswith("http://")):
                raise ValueError("http_json requires an absolute http(s) URL")
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                raise ValueError("Unsupported HTTP method")
