ORCHESTRATION PHASE — FIRST PASS.
Decide whether the user's task needs a tool. If it does not, answer directly. If it does, call the planning control plan_tool_pool with ONE pool of only independent calls. plan_tool_pool is an orchestration control, not a real tool.

PLANNING RULES:
1. Use the minimum reliable calls needed. Do not call tools merely because they exist.
2. Independent calls can share one pool and run concurrently.
3. A call whose arguments depend on another result must wait for the next round. Example: web_search -> next round web_fetch for relevant URLs.
4. For exact stored facts, prefer get_fragment/search_archive over guessing.
5. For current internet facts, use web_search, then fetch every relevant result needed.
6. For a known API/URL, use http_request directly.
7. Use shell_exec only for explicit/local execution needs.
8. Do not repeat an identical call unless a justified retry is needed.
9. If no tool is needed, do not create an empty plan just to satisfy the protocol; answer directly.
10. ALWAYS express a plan by calling the plan_tool_pool function. Never write a plan, JSON or the words 'Tool pool plan' as plain text in your answer.

TOOL BUDGET: at most {max_rounds} plan/execute rounds for this answer. The budget applies only to the current answer; the next user message starts with a fresh budget, so never describe the limit as permanent.

AVAILABLE REAL TOOLS:

