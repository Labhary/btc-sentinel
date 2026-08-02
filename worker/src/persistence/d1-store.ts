import type {
  BotStore,
  CommandAuditResult,
  CommandName,
  DeliveryStatus,
  OutboxMessage,
  StatusSummary,
} from "../contracts";

function changes(result: D1Result<unknown>): number {
  const meta = result.meta as { changes?: number };
  return meta.changes ?? 0;
}

function integer(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.trunc(value);
  }
  if (typeof value === "string" && /^\d+$/.test(value)) {
    return Number.parseInt(value, 10);
  }
  return 0;
}

export class D1BotStore implements BotStore {
  constructor(private readonly database: D1Database) {}

  async beginUpdate(updateId: number, receivedAt: string, leaseUntil: string): Promise<boolean> {
    const batchResults = await this.database.batch([
      this.database
        .prepare(
          `INSERT OR IGNORE INTO telegram_updates(
             update_id, received_at, processing_status, lease_until,
             attempt_count, updated_at
           ) VALUES (?, ?, 'RECEIVED', NULL, 0, ?)`,
        )
        .bind(updateId, receivedAt, receivedAt),
      this.database
        .prepare(
          `UPDATE telegram_updates
           SET processing_status = 'PROCESSING', lease_until = ?,
               attempt_count = attempt_count + 1, updated_at = ?
           WHERE update_id = ?
             AND (
               processing_status = 'RECEIVED'
               OR (processing_status = 'PROCESSING' AND lease_until < ?)
             )`,
        )
        .bind(leaseUntil, receivedAt, updateId, receivedAt),
    ]);
    const claimed = batchResults[1];
    if (!claimed) {
      throw new Error("D1 did not return an update-claim result");
    }
    return changes(claimed) === 1;
  }

  async finishUpdate(updateId: number, status: "COMPLETED" | "IGNORED", at: string): Promise<void> {
    await this.database
      .prepare(
        `UPDATE telegram_updates
         SET processing_status = ?, lease_until = NULL, updated_at = ?
         WHERE update_id = ? AND processing_status = 'PROCESSING'`,
      )
      .bind(status, at, updateId)
      .run();
  }

  async getSignalGenerationPaused(): Promise<boolean> {
    const row = await this.database
      .prepare("SELECT setting_value FROM bot_settings WHERE setting_key = ?")
      .bind("SIGNAL_GENERATION_PAUSED")
      .first<{ setting_value: string }>();
    return row?.setting_value === "true";
  }

  async setSignalGenerationPaused(paused: boolean, at: string): Promise<void> {
    await this.database
      .prepare(
        `INSERT INTO bot_settings(setting_key, setting_value, updated_at, row_version)
         VALUES ('SIGNAL_GENERATION_PAUSED', ?, ?, 1)
         ON CONFLICT(setting_key) DO UPDATE SET
           setting_value = excluded.setting_value,
           updated_at = excluded.updated_at,
           row_version = bot_settings.row_version + 1`,
      )
      .bind(paused ? "true" : "false", at)
      .run();
  }

  async getStatusSummary(): Promise<StatusSummary> {
    const counts = await this.database
      .prepare(
        `SELECT
           COALESCE(SUM(CASE WHEN lifecycle_status = 'PENDING' THEN 1 ELSE 0 END), 0)
             AS pending_signals,
           COALESCE(SUM(CASE WHEN lifecycle_status = 'ACTIVE' THEN 1 ELSE 0 END), 0)
             AS active_signals,
           COALESCE(SUM(CASE WHEN lifecycle_status = 'CLOSED' THEN 1 ELSE 0 END), 0)
             AS closed_signals,
           COALESCE(SUM(CASE WHEN lifecycle_status = 'EXPIRED' THEN 1 ELSE 0 END), 0)
             AS expired_signals,
           COALESCE(SUM(CASE WHEN lifecycle_status = 'CANCELLED' THEN 1 ELSE 0 END), 0)
             AS cancelled_signals
         FROM signals`,
      )
      .first<Record<string, unknown>>();
    const health = await this.database
      .prepare(
        `SELECT status, COALESCE(finished_at, started_at) AS health_at
         FROM health_runs ORDER BY started_at DESC LIMIT 1`,
      )
      .first<{ status: string; health_at: string }>();

    return {
      pendingSignals: integer(counts?.pending_signals),
      activeSignals: integer(counts?.active_signals),
      closedSignals: integer(counts?.closed_signals),
      expiredSignals: integer(counts?.expired_signals),
      cancelledSignals: integer(counts?.cancelled_signals),
      latestHealthStatus: health?.status ?? null,
      latestHealthAt: health?.health_at ?? null,
    };
  }

  async prepareOutbox(message: OutboxMessage): Promise<DeliveryStatus> {
    await this.database
      .prepare(
        `INSERT OR IGNORE INTO outbox(
           outbox_id, signal_id, message_type, payload_json, delivery_status,
           dedupe_key, attempt_count, available_at, created_at, updated_at
         ) VALUES (?, NULL, ?, ?, 'PENDING', ?, 0, ?, ?, ?)`,
      )
      .bind(
        message.outboxId,
        message.messageType,
        JSON.stringify({ chat_id: message.chatId, text: message.text }),
        message.dedupeKey,
        message.createdAt,
        message.createdAt,
        message.createdAt,
      )
      .run();
    const row = await this.database
      .prepare("SELECT delivery_status FROM outbox WHERE dedupe_key = ?")
      .bind(message.dedupeKey)
      .first<{ delivery_status: DeliveryStatus }>();
    if (!row) {
      throw new Error("Outbox row could not be prepared");
    }
    return row.delivery_status;
  }

  async markOutboxUnknown(outboxId: string, at: string): Promise<void> {
    await this.database
      .prepare(
        `UPDATE outbox
         SET delivery_status = 'UNKNOWN', attempt_count = attempt_count + 1, updated_at = ?
         WHERE outbox_id = ? AND delivery_status = 'PENDING'`,
      )
      .bind(at, outboxId)
      .run();
  }

  async markOutboxSent(outboxId: string, telegramMessageId: string, at: string): Promise<void> {
    await this.database
      .prepare(
        `UPDATE outbox
         SET delivery_status = 'SENT', telegram_message_id = ?, updated_at = ?
         WHERE outbox_id = ? AND delivery_status = 'UNKNOWN'`,
      )
      .bind(telegramMessageId, at, outboxId)
      .run();
  }

  async markOutboxFailed(outboxId: string, errorCode: string, at: string): Promise<void> {
    await this.database
      .prepare(
        `UPDATE outbox
         SET delivery_status = 'FAILED', last_error_code = ?, updated_at = ?
         WHERE outbox_id = ? AND delivery_status = 'UNKNOWN'`,
      )
      .bind(errorCode, at, outboxId)
      .run();
  }

  async recordCommand(
    updateId: number,
    command: CommandName,
    occurredAt: string,
    result: CommandAuditResult,
  ): Promise<void> {
    await this.database
      .prepare(
        `INSERT OR IGNORE INTO command_audit(
           audit_id, update_id, command, occurred_at, result
         ) VALUES (?, ?, ?, ?, ?)`,
      )
      .bind(`telegram-command-${updateId}`, updateId, command, occurredAt, result)
      .run();
  }
}
