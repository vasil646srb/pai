# BUILD 11: Telegram (public and private channel)

Install from the project root:
    unzip -o laya_integration_v11.zip
    python apply_telegram.py .
    python -m pytest -q tests_laya/test_telegram.py tests_laya/test_telegram_routes.py

"Telegram" page (in the menu):
- bot token: stored in secrets (`TELEGRAM_BOT_TOKEN`), only its tail is shown in
the UI;
- public channel: `@username` or `-100…`, with a "confirmation" mode (default) or
"immediate";
- private channel: "immediate" mode (default) or "confirmation";
- "Check bot and channels": verifies the token and the bot's posting permission
in each channel;
- queue: posts for the public channel (and the private one, if confirmation is
selected) wait for "Publish" / "Reject".

Model tool: `telegram_post (channel: public|private, text)`. Enable it in the
settings of the desired persona (Personas -> tools). A public post is not published
immediately by the model: it goes into the queue.

How to create a bot and grant permissions:
1. In @BotFather: /newbot, save the token.
2. Add the bot as admin to both channels, with the "Post messages" right.
3. Public channel: provide @username. Private channel: its numeric id (usually
   -100…), discoverable via the bot or via "Check" if the id is already known.

Env: `PAI_TELEGRAM_API` — base Bot API URL (default https://api.telegram.org).