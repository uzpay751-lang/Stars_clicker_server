# Stars Clicker API + Telegram bot

This is a starter backend for the Stars Clicker Mini App. It includes server-authoritative balances/click limits, SQLite storage, Telegram Mini App `initData` verification, XTR invoice links, payment confirmation, and admin withdrawal-request buttons.

## Important before launch
- Never share your BotFather token in chat or put it in frontend HTML. Set `BOT_TOKEN` as a private environment variable on your host.
- `ADMIN_ID` defaults to `8836465969`; change it if that is not the Telegram numeric ID of the admin.
- Set `WEBAPP_URL` to your deployed Mini App URL.
- Set `PRICE_40`, `PRICE_50`, `PRICE_70` to your desired whole XTR prices. Telegram Stars prices are integers.
- The current clicker balance is an internal game balance. It is NOT automatically convertible to real Telegram Stars. Admin must review and manually fulfill approved withdrawals. Do not promise a payout unless you have a real payout process and sufficient funds.
- SQLite is suitable for a small single-instance host with persistent disk. Use persistent storage; ephemeral deployments may erase the database.
- Do not run multiple bot polling instances with the same token.

## Run locally
1. Install Python 3.11+.
2. Create a virtual environment and install:
   `pip install -r requirements.txt`
3. Set environment variables from `.env.example` in your shell/hosting dashboard.
4. Run `python main.py`.
5. Open `http://localhost:8000/` to verify the API starts.

The bot uses long polling, so the host must allow a continuously running process. The Mini App frontend must call this API and send the Telegram `initData` string in the `Authorization: tma <initData>` header. The HTML generated earlier is not yet wired to these API routes; it must be updated before server-authoritative clicks, invoices, and withdrawals work end-to-end.

## API
- `GET /` health/status
- `GET /api/me` authenticated user state
- `POST /api/click` award one click (server checks daily limit)
- `POST /api/upgrade` buy next level using game balance
- `POST /api/payments/invoice` JSON `{ "cap": 40 }`, returns `invoice_url`
- `POST /api/withdraw` JSON `{ "amount": 10, "note": "@username" }`

## Current scope / limitations
- XTR payment purchase updates the user's daily click cap after Telegram confirms `successful_payment`.
- Purchase resets clicks used for the day; configure different product semantics if desired.
- Admin approve/reject updates the request status and sends a notice, but does NOT transfer Stars automatically.
- Admin can receive requests only after a valid BotFather token is configured and the bot can message the admin (admin should start the bot once).
