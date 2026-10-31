# Padel match notifications

This checker looks for open Padel matches whose listed level is
exactly **3.50–4.00**, start at or after **20:00** UK time, and still have a
free spot. It sends a Telegram message when it sees a match ID for the first
time. It checks the club's public matches page; it does not log in, book
matches, or read player details.

## Run locally

Requires Python 3.12 or later. No third-party Python packages are needed. The
local computer should use UK time; GitHub Actions is configured for UK time.

```powershell
python -m unittest -v
$env:MATCHES_SEARCH_URL = "<matches search page URL>"
$env:MATCH_DETAILS_URL = "<match details page URL>"
python check_matches.py --dry-run
```

The URL environment variables are needed for a local run. The dry run fetches
the public page and prints matching matches without sending messages or
changing the notification state.

## Run with GitHub Actions

1. Put these files in a GitHub repository and make sure the workflow is on the
   repository's default branch.
2. In Telegram, create a bot with [@BotFather](https://t.me/BotFather), then
   open a chat with the bot and send `/start`.
3. Find the chat ID for that conversation using Telegram's Bot API
   `getUpdates` method. Keep the bot token private; do not put it in source
   code, an issue, or a chat message.
4. In the GitHub repository, open **Settings → Secrets and variables →
   Actions** and add these repository secrets:
   - `MATCHES_SEARCH_URL`: the configured site's public matches search page URL.
   - `MATCH_DETAILS_URL`: the configured site's match details page URL.
   - `TELEGRAM_BOT_TOKEN`: the token provided by BotFather.
   - `TELEGRAM_CHAT_ID`: the ID of your Telegram conversation.
5. In **Settings → Actions → General**, allow GitHub Actions to create and
   approve pull requests is not needed. Under **Workflow permissions**, select
   **Read and write permissions** so the workflow can save its notification
   state.
6. Under the **Actions** tab, enable workflows if prompted. You can start one
   run manually with **Check padel matches → Run workflow**.

The first successful run sends alerts for every currently matching match it
finds, then records those IDs to prevent repeats.

The workflow is triggered hourly during the UTC hours needed to cover **09:00
through 23:00 UK local time** in both GMT and BST. The checker only fetches the
site during that local-time window; off-window triggers skip the check. GitHub
schedule times are best-effort and can be delayed, so this is not a guaranteed
real-time alert. The workflow saves recently alerted match IDs in
`seen_matches.json` to avoid repeated notifications. That file is updated by
the workflow when it finds new matches.
