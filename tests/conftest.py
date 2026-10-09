import os

# The answer check uses the summarizer model; in tests it is usually the same mock as the main
# model, which would consume its scripted replies. Tests opt in explicitly where they need it.
os.environ.setdefault("PAI_VERIFY", "0")
os.environ.setdefault("PAI_SERVICES", "0")
