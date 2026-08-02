import type { Clock } from "./contracts";

export const systemClock: Clock = {
  now: () => new Date(),
};

export function isoUtc(value: Date): string {
  if (Number.isNaN(value.getTime())) {
    throw new Error("Invalid date");
  }
  return value.toISOString();
}

export function addMinutes(value: Date, minutes: number): Date {
  return new Date(value.getTime() + minutes * 60_000);
}

export function formatCasablanca(value: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Casablanca",
    dateStyle: "medium",
    timeStyle: "medium",
    hour12: false,
  }).format(value);
}
