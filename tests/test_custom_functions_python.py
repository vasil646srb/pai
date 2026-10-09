import os

from pai.custom_functions import CustomFunction, CustomFunctionRegistry


def test_python_custom_function_executes_only_after_activation(tmp_path):
    registry = CustomFunctionRegistry(tmp_path / "functions.json")
    code = """
import os

def create_file(folder_path, file_name, content):
    os.makedirs(folder_path, exist_ok=True)
    path = os.path.join(folder_path, file_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": path}
"""
    fn = CustomFunction(
        name="create_file",
        description="Create a file",
        parameters={"type": "object", "properties": {}},
        kind="python",
        implementation={"inline_code": code},
    )
    registry.add(fn)
    assert fn.status == "draft"
    registry.validate(fn.id)
    registry.activate(fn.id)
    result = registry.execute(
        "create_file",
        {"folder_path": str(tmp_path / "out"), "file_name": "x.txt", "content": "hello"},
    )
    assert result["execution"] == "python_subprocess"
    assert (tmp_path / "out" / "x.txt").read_text(encoding="utf-8") == "hello"


def test_python_custom_function_normalizes_uppercase_json_schema(tmp_path):
    registry = CustomFunctionRegistry(tmp_path / "functions.json")
    fn = CustomFunction(
        name="subtract_numbers",
        description="Subtract two numbers",
        parameters={
            "type": "OBJECT",
            "required": ["num1", "num2"],
            "properties": {
                "num1": {"type": "NUMBER"},
                "num2": {"type": "NUMBER"},
            },
        },
        kind="python",
        implementation={
            "inline_code": "def subtract_numbers(num1, num2):\n    return num1 - num2"
        },
    )
    registry.add(fn)
    registry.validate(fn.id)
    assert fn.parameters["type"] == "object"
    assert fn.parameters["properties"]["num1"]["type"] == "number"
    registry.activate(fn.id)
    result = registry.execute("subtract_numbers", {"num1": 10, "num2": 3})
    assert result["result"] == 7
