type EventRecord = Record<string, any>;
const readable = (value: string) => value.replace(/[._-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
export function eventTitle(event: EventRecord): string {
  const task = event.payload?.title || event.payload?.capability;
  return task ? `${readable(task)} · ${readable(event.kind || event.event_type || "Event")}` : readable(event.kind || event.event_type || "Event");
}
export function eventMessage(event: EventRecord): string {
  const payload = event.payload || {};
  for (const value of [payload.message, payload.reason, payload.summary, event.message, event.reason]) {
    if (typeof value === "string" && value.trim()) return value;
  }
  const kind = event.kind || event.event_type || "";
  const descriptions: Record<string, string> = {
    "action.requested": "Task accepted and queued.", "action.started": "Task is running.",
    "action.output": "New task output is available.", "action.succeeded": "Task completed successfully.",
    "action.failed": "Task failed. Open details for the reported reason.", "action.refused": "Task was refused by a validation or safety check.",
    "action.cancelled": "Task was cancelled.", "action.cancel_requested": "Cancellation requested.",
    "action.outcome_unknown": "Task outcome needs review before any retry.",
  };
  return descriptions[kind] || "Source observation recorded. Open details for supporting information.";
}
export function eventTime(value: unknown): string {
  if (!value) return "Time unavailable";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
}
