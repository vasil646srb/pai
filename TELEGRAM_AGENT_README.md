# BUILD 13: Telegram — background replies in a group (Jev + the brain persona)

Install from the project root (builds 11 and 12 must be applied):
    unzip -o laya_integration_v13.zip
    python apply_telegram_agent.py .
    python -m pytest -q tests_laya

How it works in the background (every `PAI_TELEGRAM_POLL_SEC` seconds, default 60):
1. `getUpdates` — new messages from the group and the channels being read,
   without duplicates.
2. Jev classifies each new message: whether a reply is needed (yes/no, threshold
   0.6) and its type (question, request, chatter, spam).
3. If a reply is needed: the `brain` persona (a separate `telegram-group` space)
   writes the text, which is put into the group queue. In the group's
   "confirmation" mode you press "Publish". In "immediate" mode it goes out on
   its own.
4. Spam, bot messages, and messages that need no reply are skipped. Each message
   is processed once.

Requirements: the Laya/Jev integration (Laya builds, `PAI_DECISION_PROVIDER=jev`
and a key). Without it, classification returns "unavailable", the message is
retried 3 times and flagged, and no replies are produced.

Telegram page:
- "Reply in the group automatically" — the auto-reply toggle.
- "Process now" — manually run the queue of new messages.
- "Write to a channel or group" — manual sending: private goes immediately,
  public and group go through confirmation.

Sending examples:
- In chat: "publish to the public channel: …" — the assistant calls
  `telegram_post`, the post awaits confirmation.
- In chat: "note this to your private channel: …" — goes immediately.
- On the page: the "Write to a channel or group" form.
- Programmatically (local): `POST /telegram/send` with body `{"channel": "private", "text": "…"}`.