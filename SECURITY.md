# Security policy

## Report a problem

Do not open a public issue containing a token, API key, user ID, database ID,
request signature, or log excerpt with credentials. Revoke the exposed value
first, then report the problem without reproducing the secret.

## Secret locations

- GitHub Actions secrets: engine-to-state-API signing secret and deployment
  identifiers that the engine truly needs.
- Cloudflare encrypted secrets: Telegram bot token, Telegram webhook secret,
  and state-API signing secret.
- D1: operational data only. No Telegram tokens or API credentials.
- Repository: placeholder names only.

The first version never uses a Binance private API key. It requests public
market data and cannot place, modify, or cancel an order.

## If a Telegram token is exposed

1. Open Telegram and contact `@BotFather`.
2. Revoke the compromised token.
3. Generate a replacement.
4. Replace it in the encrypted secret store only.
5. Redeploy the command Worker.
6. Review bot messages and platform logs from the exposure window.

Do not paste the replacement token into a chat, issue, commit, or screenshot.

