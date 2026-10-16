# BUILD 12: Telegram — group and new messages

Install from the project root (build 11 must be applied; re-running is safe):
    unzip -o laya_integration_v12.zip
    python apply_telegram.py .            # already applied — will be skipped
    python apply_telegram_groups.py .
    python -m pytest -q tests_laya        # 13 tests

What was added:
- A group as a third channel: chat_id, publishing mode (confirmation by default),
  reading enabled.
- Reading: `getUpdates`. Background polling every 60 s (`PAI_TELEGRAM_POLL_SEC`,
  0 = off), plus a "Check new messages" button. Messages are stored in
  `telegram_inbox.json`, the offset in `telegram_state.json`. Re-polling never
duplicates.
- Tool `telegram_read (channel: all|group|public|private)`: returns unread
  messages and marks them as read.
- `telegram_post` now accepts `channel = group` (publication to the group through
the confirmation queue).

Important notes on the group:
- By default a bot in a group only sees commands, mentions, and replies to its own
  messages. To see all messages: @BotFather → /setprivacy → Disable, then remove
the bot from the group and add it again.
- To read a channel (public/private) the bot must be the channel admin. For a
  private channel the chat_id can be obtained via getUpdates (the bot receives a
  channel_post after being added as admin).
- The webhook must be disabled (deleteWebhook), otherwise getUpdates returns a
  conflict error.

How to use: ask the assistant "anything new in the group?" — it will call
`telegram_read`. To reply in the group, the assistant puts the post into the group
queue and you confirm it on the Telegram page.