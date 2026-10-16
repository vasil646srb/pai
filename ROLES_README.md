# BUILD 10: models per task

From the project root:
    unzip -o laya_integration_v10.zip
    python apply_roles.py .
    python -m pytest -q tests_laya/test_roles.py     (needs: the pai package, fastapi, httpx)

What it does:
- New field "Pool planning model" in Settings -> LLM (empty = the main model).
- The `planning` and `analysis` phase calls (tool selection and result parsing)
  run on this model.
- The final answer (`final`) stays on the main model.
- Summarization and answer verification keep their own models, as before
  (`summarizer_model`, `verify_model`).
- The value is stored in `llm_config.json` and applied at startup and on save
  without restart.

Verification at runtime: in the "Action log", the model field of the `llm_request`
rows must match the role (planning/analysis -> planner_model, final -> model).