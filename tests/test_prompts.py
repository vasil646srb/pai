from pathlib import Path

from pai.personas import BUILTIN_ADDONS
from pai.prompts import __file__ as prompts_init
from pai.prompts import load_prompt


def test_loader_strips_exactly_one_trailing_newline():
    folder = Path(prompts_init).parent
    files = list(folder.rglob("*.md"))
    assert files
    for path in files:
        name = path.relative_to(folder).with_suffix("").as_posix()
        raw = path.read_text(encoding="utf-8")
        assert raw.endswith("\n"), name
        assert load_prompt(name) == raw[:-1], name
        assert load_prompt(name).strip(), name


def test_builtin_addons_come_from_files():
    assert BUILTIN_ADDONS["brain"] == load_prompt("addons/brain")


def test_missing_prompt_is_an_error():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist")
