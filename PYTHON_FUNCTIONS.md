# Python custom functions

Custom functions support `kind: "python"`, which lets the assistant run arbitrary
Python code defined by the owner.

## Definition format

The implementation is stored in `implementation.inline_code` and must contain a
function with the same name as the function's `name`:

```json
{
  "kind": "python",
  "name": "create_file",
  "implementation": {
    "inline_code": "import os\n\ndef create_file(folder_path, file_name, content):\n    os.makedirs(folder_path, exist_ok=True)\n    with open(os.path.join(folder_path, file_name), 'w', encoding='utf-8') as f:\n        f.write(content)\n    return {'ok': True}"
  }
}
```

## Workflow

1. The LLM calls `propose_function`.
2. The function is saved as a `draft`, and its code is **not** executed.
3. The owner clicks **Validate**.
4. After a successful validation the owner clicks **Confirm and activate**.
5. Only an **active** Python function appears in the tool schemas and can be
   invoked by the model.
6. On invocation, the code runs in a separate Python process.

## Process limits

- Timeout: 30 seconds by default (maximum 120).
- stdout size cap: 200 000 characters (maximum 1 000 000).

## Security notice (important)

This is **not** a full security sandbox. Enabled Python code runs with the OS
privileges of the user who runs PAI and can import any available Python module,
touch the filesystem, and reach the network. Therefore, clicking **activate**
is an explicit grant of code-execution permission. Only enable functions you
have reviewed and trust.