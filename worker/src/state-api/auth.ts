import type { BotStore, Clock } from "../contracts";
import { isoUtc } from "../time";

const encoder = new TextEncoder();
const MAX_CLOCK_SKEW_SECONDS = 300;
const NONCE_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;
const SIGNATURE_PATTERN = /^[a-f0-9]{64}$/;

function hex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256(body: Uint8Array): Promise<string> {
  const copy = new ArrayBuffer(body.byteLength);
  new Uint8Array(copy).set(body);
  return hex(await crypto.subtle.digest("SHA-256", copy));
}

async function hmac(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
}

function fixedTimeHexEquals(left: string, right: string): boolean {
  if (!SIGNATURE_PATTERN.test(left) || !SIGNATURE_PATTERN.test(right)) {
    return false;
  }
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

export async function authorizeStateRequest(
  request: Request,
  body: Uint8Array,
  secret: string,
  store: BotStore,
  clock: Clock,
): Promise<boolean> {
  const timestampText = request.headers.get("x-btc-timestamp") ?? "";
  const nonce = request.headers.get("x-btc-nonce") ?? "";
  const signature = request.headers.get("x-btc-signature") ?? "";
  if (!/^\d{10}$/.test(timestampText) || !NONCE_PATTERN.test(nonce)) {
    return false;
  }
  const timestamp = Number.parseInt(timestampText, 10);
  const nowSeconds = Math.floor(clock.now().getTime() / 1000);
  if (Math.abs(nowSeconds - timestamp) > MAX_CLOCK_SKEW_SECONDS) {
    return false;
  }
  const url = new URL(request.url);
  if (url.search !== "") {
    return false;
  }
  const canonical = [
    request.method.toUpperCase(),
    url.pathname,
    timestampText,
    nonce,
    await sha256(body),
  ].join("\n");
  const expected = await hmac(secret, canonical);
  if (!fixedTimeHexEquals(signature, expected)) {
    return false;
  }
  const expiresAt = isoUtc(new Date((timestamp + MAX_CLOCK_SKEW_SECONDS + 1) * 1000));
  return store.claimStateNonce(nonce, expiresAt, isoUtc(clock.now()));
}

export async function signStateRequestForTest(
  method: string,
  path: string,
  timestamp: string,
  nonce: string,
  body: Uint8Array,
  secret: string,
): Promise<string> {
  return hmac(
    secret,
    [method.toUpperCase(), path, timestamp, nonce, await sha256(body)].join("\n"),
  );
}
