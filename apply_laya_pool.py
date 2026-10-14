#!/usr/bin/env python3
"""СБОРКА 8. Shadow: Jev голосует за каждый инструмент до ответа планировщика (фоновый поток, без задержки).
Результат -> action_log kind='laya', name='pool_plan'. Поведение планировщика не меняется.
Запускать из корня проекта ПОСЛЕ apply_laya.py. Anchor-based, all-or-nothing, резервная копия <file>.pre-laya-pool.
Повторный запуск безопасен."""
import sys
from pathlib import Path

EDITS = {'pai/orchestrator/dialog.py': [('import',
                                 'from ..laya import get_laya, log_shadow\n',
                                 'from ..laya import get_laya, log_shadow, laya_pool_shadow, last_user_text\n'),
                                ('planner hook',
                                 '            pool = self._normalize_pool(pool, tool_schemas)\n',
                                 '            pool = self._normalize_pool(pool, tool_schemas)\n'
                                 '            if iteration == 0:  # shadow: Jev votes per tool, background thread, '
                                 'planner unaffected\n'
                                 '                laya_pool_shadow(self.storage, space_id, last_user_text(messages), '
                                 'tool_schemas, pool)\n')]}
MARK = {"pai/orchestrator/dialog.py": "laya_pool_shadow"}

def main(root="."):
    root = Path(root); bad = 0
    for rel, ed in EDITS.items():
        f = root / rel
        if not f.is_file():
            print(f"SKIP  {rel}: no such file"); bad += 1; continue
        s = f.read_text(encoding="utf-8")
        if MARK[rel] in s:
            print(f"OK    {rel}: already patched"); continue
        miss = [name for name, old, new in ed if s.count(old) != 1]
        if miss:
            print(f"FAIL  {rel}: anchors not found exactly once: {miss} (file untouched)"); bad += 1; continue
        f.with_name(f.name + ".pre-laya-pool").write_text(s, encoding="utf-8")
        for name, old, new in ed:
            s = s.replace(old, new, 1)
        f.write_text(s, encoding="utf-8"); print(f"PATCH {rel}: {len(ed)} edits")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
