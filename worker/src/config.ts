export interface WorkerEnv {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_ADMIN_USER_ID: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  STATE_API_HMAC_SECRET: string;
}

export interface WorkerConfig {
  telegramBotToken: string;
  telegramAdminUserId: string;
  telegramWebhookSecret: string;
  stateApiHmacSecret: string;
}

export class ConfigurationError extends Error {
  override readonly name = "ConfigurationError";
}

function required(value: string | undefined, name: string, minimumLength: number): string {
  if (!value || value.length < minimumLength || value.includes("REPLACE")) {
    throw new ConfigurationError(`${name} is not configured`);
  }
  return value;
}

export function loadConfig(env: WorkerEnv): WorkerConfig {
  const adminId = required(env.TELEGRAM_ADMIN_USER_ID, "TELEGRAM_ADMIN_USER_ID", 1);
  const numericAdminId = Number(adminId);
  if (!/^\d{1,16}$/.test(adminId) || !Number.isSafeInteger(numericAdminId) || numericAdminId <= 0) {
    throw new ConfigurationError("TELEGRAM_ADMIN_USER_ID is invalid");
  }

  const token = required(env.TELEGRAM_BOT_TOKEN, "TELEGRAM_BOT_TOKEN", 30);
  if (!/^\d{6,12}:[A-Za-z0-9_-]{20,}$/.test(token)) {
    throw new ConfigurationError("TELEGRAM_BOT_TOKEN is invalid");
  }

  const webhookSecret = required(env.TELEGRAM_WEBHOOK_SECRET, "TELEGRAM_WEBHOOK_SECRET", 32);
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(webhookSecret)) {
    throw new ConfigurationError("TELEGRAM_WEBHOOK_SECRET is invalid");
  }
  const hmacSecret = required(env.STATE_API_HMAC_SECRET, "STATE_API_HMAC_SECRET", 32);
  if (webhookSecret === hmacSecret) {
    throw new ConfigurationError("Webhook and state API secrets must differ");
  }

  return {
    telegramBotToken: token,
    telegramAdminUserId: adminId,
    telegramWebhookSecret: webhookSecret,
    stateApiHmacSecret: hmacSecret,
  };
}
