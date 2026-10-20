"""Tool schemas (OpenAI function-calling format) and persona/tool constants."""

from __future__ import annotations

import re

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_fragment",
            "description": "Get the exact content of a memory fragment by ID.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_archive",
            "description": "Full-text search across the archive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "space_id": {"type": "string"},
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_time_delta",
            "description": "Compute the difference between two dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date1": {"type": "string"},
                    "date2": {"type": "string"},
                },
                "required": ["date1", "date2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_fragments_by_timeframe",
            "description": "List fragments within a time range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "space_id": {"type": "string"},
                },
                "required": ["date_from", "date_to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo). Returns {title, url, snippet}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_search",
            "description": "Search Wikipedia articles. Returns {title, url, snippet}. Good for definitions and background facts. Use web_fetch on the url for the full article.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                    "lang": {"type": "string", "default": "ru", "description": "Wikipedia language code, e.g. ru, en, de."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pubmed_search",
            "description": "Search PubMed biomedical literature. Returns {title, url, snippet} with authors, journal and date in snippet. For the abstract, use web_fetch on the url.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "English keywords work best, e.g. 'metformin aging mice'."},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_search",
            "description": "Search freely licensed images on Wikimedia Commons. Returns {title, markdown, image_url, page_url, license, author}. To show a picture, copy the 'markdown' field of a result verbatim on its own line (it embeds image_url). Never use page_url as an image: it is a web page. Cite license and author if asked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Follow a link (e.g. from web_search results) and get the "
                "page's content. The mode parameter controls what's returned: "
                "'text' — page text with tags stripped, 'html' — the raw "
                "HTML source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full page URL, including scheme (https://...)."},
                    "mode": {
                        "type": "string",
                        "enum": ["text", "html"],
                        "default": "text",
                        "description": "'text' — readable text only, 'html' — raw HTML.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "default": 8000,
                        "description": "Maximum length of the returned content, in characters.",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": (
                "Send a direct HTTP request with requests. Supports GET, POST, PUT, "
                "PATCH and DELETE. Use headers, query params, JSON body or raw body "
                "when needed. Returns status, headers and parsed JSON/text response. "
                "For the main persona, local and LAN addresses are allowed, and cookies persist between calls, "
                "so a login is done once. Saved secrets are inserted as {{secret:NAME}} in url, headers, params, "
                "body or string values of json; the real value is never shown to you and is masked in the reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                    "url": {"type": "string"},
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "params": {"type": "object"},
                    "json": {"type": "object", "description": "JSON request body for POST/PUT/PATCH."},
                    "body": {"type": "string", "description": "Raw request body; use instead of json."},
                    "timeout": {"type": "number", "default": 20},
                    "max_chars": {"type": "integer", "default": 200000},
                },
                "required": ["method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": (
                "Execute a local command. runtime='bash' runs bash -lc, runtime='termux' "
                "runs the Termux shell, and runtime='python3' runs python3 -c with the "
                "command as Python source. Use only when the user explicitly needs local "
                "execution. Output is capped and the process has a timeout."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "runtime": {"type": "string", "enum": ["bash", "termux", "python3"], "default": "bash"},
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                    "max_output_chars": {"type": "integer", "default": 50000},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_function",
            "description": (
                "Propose a new custom function. The function is created only as a "
                "draft and does not become active without explicit owner confirmation "
                "on the 'Functions' page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique function name in snake_case/Latin script."},
                    "description": {"type": "string"},
                    "parameters": {"type": "object", "description": "JSON Schema of the input parameters."},
                    "kind": {
                        "type": "string",
                        "enum": ["llm_prompt", "http_json", "python", "dynamic"],
                        "description": "python allows running arbitrary inline_code only after validate and explicit activate by the owner.",
                    },
                    "implementation": {
                        "type": "object",
                        "description": 'For python: {"inline_code": "def function_name(...): ..."}. The code runs only after validate + activate.',
                    },
                },
                "required": ["name", "description", "parameters", "kind", "implementation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_rule",
            "description": (
                "Propose a new self-generated behavior rule (a pattern noticed in the "
                "dialogue). The rule is created only as a draft and does not become "
                "active without explicit owner confirmation on the 'Rules' page. If "
                "scope='persona' and persona_id is not given, the rule is automatically "
                "attached to the currently active persona."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_text": {
                        "type": "string",
                        "description": "The rule text in plain language, e.g.: 'If the user writes \u201cplan for the day\u201d, show open commitments first.'",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["global", "persona", "space", "project"],
                        "default": "global",
                        "description": "global — always applies; persona — only for the current/given persona; space/project — only for a specific space.",
                    },
                    "space_id": {"type": "string", "description": "Required when scope='space' or 'project'."},
                    "trigger_context": {
                        "type": "string",
                        "description": "Brief description of the situation/context in which this rule was noticed.",
                    },
                    "confidence": {
                        "type": "number",
                        "default": 0.5,
                        "description": "The model's confidence in the rule's usefulness, from 0 to 1.",
                    },
                    "derived_from": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of memory fragments the rule was derived from (if any).",
                    },
                },
                "required": ["rule_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": (
                "Load the full instructions of one of the active skills listed in the "
                "system prompt. Call it BEFORE acting when a skill's trigger matches the "
                "user's request, then follow the returned instructions."
            ),
            "parameters": {
                "type": "object",
                "properties": {"skill_id": {"type": "string", "description": "Skill id from the active skills list."}},
                "required": ["skill_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Persona management: only the Second Brain persona has these schemas, and they
# are never granted through tool_permissions.
# ---------------------------------------------------------------------------
_PERSONA_FIELD_PROPS = {
    "name": {"type": "string", "description": "Display name (max 80 chars)."},
    "description": {"type": "string", "description": "What the persona is for (max 1000 chars)."},
    "tone": {"type": "string", "description": "Tone, e.g. 'neutral', 'friendly' (max 100 chars)."},
    "system_core_addon": {"type": "string", "description": "Persona instructions (max 6000 chars)."},
    "active_skill_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Skill ids from the skills list.",
    },
    "allow_spaces": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Memory spaces the persona may read. Empty = all.",
    },
    "deny_spaces": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Memory spaces hidden from the persona.",
    },
    "allow_tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Only memory with these tags. Empty = any.",
    },
    "deny_tags": {"type": "array", "items": {"type": "string"}, "description": "Memory with these tags is hidden."},
    "tool_permissions": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tools the persona may use. Cannot include shell_exec or persona-management tools.",
    },
}


PERSONA_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_personas",
            "description": "List all personas with their description, tone, tool permissions and memory filters.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_persona",
            "description": "Full settings of one persona, including its instructions (system_core_addon) and skills.",
            "parameters": {
                "type": "object",
                "properties": {"persona_id": {"type": "string"}},
                "required": ["persona_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_persona",
            "description": "Create a new persona. Use only when the owner explicitly asks. New personas get "
            "read-only memory and web tools by default; shell_exec can only be granted by the owner in the UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "[a-z][a-z0-9_]{1,31}"},
                    **_PERSONA_FIELD_PROPS,
                },
                "required": ["persona_id", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_persona",
            "description": "Change settings of an existing persona (not Second Brain). Pass only the fields to change. "
            "Use only when the owner explicitly asks.",
            "parameters": {
                "type": "object",
                "properties": {"persona_id": {"type": "string"}, **_PERSONA_FIELD_PROPS},
                "required": ["persona_id"],
            },
        },
    },
]


TOOL_SCHEMAS.extend(PERSONA_TOOL_SCHEMAS)
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "telegram_read",
        "description": "Check the owner's Telegram group and reading channels for new messages. Returns the "
                        "unread ones and marks them read. Use it when asked about new messages or before answering in the group.",
        "parameters": {"type": "object", "properties": {
            "channel": {"type": "string", "enum": ["all", "group", "public", "private"], "description": "Which chat to check."},
        }, "required": []},
    },
})
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "telegram_post",
        "description": "Publish a text post to one of the owner's Telegram channels. 'private' is the owner's "
                        "private channel and goes out at once. 'public' is the public channel: the owner gets the draft "
                        "in Telegram with buttons (publish / revise / cancel) and it goes out only after the owner "
                        "presses publish. Tell the owner the draft is waiting for approval. "
                        "Plain text, up to 4000 characters.",
        "parameters": {"type": "object", "properties": {
            "channel": {"type": "string", "enum": ["public", "private", "group"], "description": "Which channel."},
            "text": {"type": "string", "description": "Post text."},
        }, "required": ["channel", "text"]},
    },
})

TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "ask_confirmation",
        "description": "Ask the owner to confirm one consequential action before it runs: an HTTP request that changes "
                        "data (POST/PUT/PATCH/DELETE) or a local shell command. The owner gets Confirm / Cancel buttons "
                        "in Telegram; if confirmed, the exact tool call runs and its result is sent to the owner. "
                        "Use this instead of calling the tool directly. Returns awaiting_confirmation; then tell the "
                        "owner the request is waiting for the buttons.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "What will be done and why, in plain words, in the owner's language. Max 500 characters."},
            "tool": {"type": "string", "enum": ["http_request", "shell_exec"], "description": "Tool to run after confirmation."},
            "arguments": {"type": "object", "description": "Exact arguments for that tool."},
        }, "required": ["question", "tool", "arguments"]},
    },
})


BRAIN_ONLY_TOOLS = frozenset({"list_personas", "get_persona", "create_persona", "edit_persona", "send_file_to_owner"})


BRAIN_PERSONA_ID = "brain"


# Tools a model may never grant to a persona it creates or edits (the owner can, in the UI).
MODEL_GRANT_DENYLIST = frozenset({"shell_exec"})


_PERSONA_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,31}")


_MAX_ADDON_CHARS = 6000


_DEFAULT_NEW_PERSONA_TOOLS = ["get_fragment", "search_archive", "calculate_time_delta", "web_search", "web_fetch"]


_PERSONA_STR_LIMITS = {"name": 80, "description": 1000, "tone": 100}


_PERSONA_LIST_FIELDS = (
    "allow_spaces",
    "deny_spaces",
    "allow_tags",
    "deny_tags",
    "tool_permissions",
    "active_skill_ids",
)


TOOL_PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "plan_tool_pool",
        "description": (
            "Orchestration-only control: plan independent real-tool calls for the next execution round. "
            "This control is never executed as a real tool. Each call must already be executable "
            "without the result of another call in the same pool. Put dependent calls into a later "
            "round. Use an empty calls array only when the task needs tools but there is no safe/useful "
            "call to make yet. If no tools are needed at all, answer directly instead of planning."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arguments": {"type": "object"},
                            "id": {"type": "string"},
                        },
                        "required": ["name", "arguments"],
                    },
                }
            },
            "required": ["calls"],
        },
    },
}

TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "send_file_to_owner",
        "description": ("Send a picture or a file to the owner's private Telegram chat. Use kind='photo' for pictures "
                        "and kind='document' for any file. Give either url (an https link on upload.wikimedia.org, "
                        "e.g. the image_url of an image_search result) or upload_id (a file already stored, e.g. one "
                        "the owner sent). Photos are sent as images; documents keep the original file name."),
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["photo", "document"]},
            "url": {"type": "string", "description": "https link on upload.wikimedia.org."},
            "upload_id": {"type": "string", "description": "Id of a stored file."},
            "caption": {"type": "string", "description": "Optional caption, up to 1000 characters."},
        }, "required": ["kind"]},
    },
})
