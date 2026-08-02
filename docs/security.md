# Security design

## Trust boundaries

Telegram, GitHub runners, Cloudflare, Binance, and every news endpoint are
separate trust boundaries. Input from all of them is parsed and validated.

## Telegram commands

- Validate the webhook secret header before parsing a command.
- Compare numeric Telegram `from.id` with the configured owner ID.
- Do not authorize by username; usernames can change.
- Accept only a fixed command allowlist and bounded arguments.
- Administrative mutations require both webhook validation and owner ID.
- Never return environment values in `/status`, logs, or errors.

## Engine state API

- Requests use HMAC-SHA256 over method, canonical path, timestamp, nonce, and
  raw-body digest.
- Reject timestamps outside a short clock-skew window.
- Store used nonces temporarily to stop replay.
- Expose typed operations, not arbitrary SQL.
- Use optimistic row versions for state transitions.
- Use a separate signing secret from the Telegram webhook secret.

## Secrets

Source code reads secret values from environment bindings. A wrapper suppresses
string conversion and `repr`. Structured-log redaction runs as a second line of
defense. The repository safety script rejects common token patterns before CI
tests.

The D1 database stores no provider credential. A Telegram chat ID is operational
personal data and is not printed in public logs.

## Least privilege

Version 1 has no Binance key at all. The GitHub workflow receives only the
state-API signing secret and necessary public configuration. D1 is accessed by
the Worker binding. Deployment credentials are used by a separate manual
deployment workflow, not by every scheduled analysis job.

## Logging

Logs use event names and opaque IDs. They may contain signal IDs, strategy
versions, timestamps, source names, HTTP status codes, and redacted errors.
They must not contain tokens, authorization headers, signed URLs, raw webhook
headers, owner IDs, or full third-party response bodies.

