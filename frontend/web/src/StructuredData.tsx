import { useMemo, useState } from "react";
export const fieldLabel = (s: string) =>
  s.replace(/[_.-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
export function summary(value: any): string {
  if (value == null) return "Not reported";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number")
    return value.toLocaleString(undefined, { maximumFractionDigits: 8 });
  if (Array.isArray(value)) return `${value.length} entries`;
  if (typeof value === "object")
    return Object.entries(value)
      .slice(0, 3)
      .map(
        ([k, v]) =>
          `${fieldLabel(k)}: ${typeof v === "object" && v !== null ? summary(v) : String(v ?? "—")}`,
      )
      .join(" · ");
  return String(value);
}
export function StructuredData({ value }: { value: any }) {
  const [limit, setLimit] = useState(25);
  if (typeof value === "string") return <OutputView output={value} />;
  if (value == null || typeof value !== "object")
    return (
      <span
        className={
          typeof value === "boolean"
            ? `data-boolean ${value ? "yes" : "no"}`
            : "data-scalar"
        }
      >
        {summary(value)}
      </span>
    );
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">No entries</span>;
    const rows = value.slice(0, limit);
    const objects = rows.every(
      (v) => v && typeof v === "object" && !Array.isArray(v),
    );
    return (
      <div className="structured-array">
        {objects ? (
          <div className="structured-records">
            {rows.map((record, index) => {
              const fields = Object.entries(record).filter(([key]) => !/(?:^|_)(?:id|hash|digest|path)$/.test(key));
              const title = record.title || record.name || record.symbol || record.strategy || record.kind || `Record ${index + 1}`;
              return <details className="structured-record" key={index}>
                <summary><strong>{summary(title)}</strong><span>{fields.slice(0, 3).filter(([, value]) => value !== title).map(([key, value]) => `${fieldLabel(key)}: ${summary(value)}`).join(" · ") || "View recorded fields"}</span></summary>
                <StructuredData value={record} />
              </details>;
            })}
          </div>
        ) : (
          <ol className="data-list">
            {rows.map((v, i) => (
              <li key={i}>
                <StructuredData value={v} />
              </li>
            ))}
          </ol>
        )}
        {limit < value.length && (
          <button onClick={() => setLimit((n) => n + 25)}>
            Show more · {value.length - limit} remaining
          </button>
        )}
      </div>
    );
  }
  return (
    <dl className="structured-data">
      {Object.entries(value).map(([k, v]) => (
        <div key={k}>
          <dt>{fieldLabel(k)}</dt>
          <dd>
            {v && typeof v === "object" ? (
              <details open={Object.keys(value).length < 5}>
                <summary>
                  {Array.isArray(v)
                    ? `${v.length} entries`
                    : `${Object.keys(v).length} fields`}
                </summary>
                <StructuredData value={v} />
              </details>
            ) : typeof v === "number" && /(?:^|_)bytes$/.test(k) ? (
              <span
                className="data-scalar"
                title={`${v.toLocaleString()} bytes`}
              >
                {v >= 1073741824
                  ? `${(v / 1073741824).toFixed(2)} GiB`
                  : `${(v / 1048576).toFixed(2)} MiB`}
              </span>
            ) : (
              <StructuredData value={v} />
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
// Decode whole documents and newline-delimited records, keeping ordinary progress messages.
export function outputParts(output: string): any[] {
  const trimmed = output.trim();
  if (!trimmed) return [];
  try {
    return [JSON.parse(trimmed)];
  } catch {}
  const lines = trimmed.split("\n");
  const parts: any[] = [];
  let triedDocument = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    if (/^[\[{]/.test(line)) {
      try {
        parts.push(JSON.parse(line));
        continue;
      } catch {}
      if (!triedDocument) {
        triedDocument = true;
        try {
          parts.push(JSON.parse(lines.slice(i).join("\n")));
          break;
        } catch {}
      }
    }
    // A bounded worker capture can end midway through a structured document.
    // Preserve complete fields without exposing JSON punctuation as log messages.
    if (/^"[^"\n]+"\s*:/.test(line)) {
      const pair = line.replace(/,$/, "");
      try {
        parts.push(JSON.parse("{" + pair + "}"));
        continue;
      } catch {}
      const key = pair.match(/^"([^"\n]+)"\s*:/)?.[1];
      if (key) {
        parts.push({
          [key]: /[\[{]$/.test(pair)
            ? "Structured section"
            : "Partial value · capture incomplete",
        });
        continue;
      }
    }
    if (/^[\[\]{}\s,]+$/.test(line)) continue;
    parts.push(line);
  }
  return parts;
}
export function OutputView({ output }: { output: string }) {
  const parts = useMemo(() => outputParts(output), [output]);
  const [limit, setLimit] = useState(50);
  if (!parts.length) return <span className="muted">No output recorded</span>;
  return (
    <div className="formatted-output">
      {parts.slice(0, limit).map((part, i) =>
        typeof part === "string" ? (
          <p className="output-message" key={i}>
            {part}
          </p>
        ) : (
          <div className="output-record" key={i}>
            {parts.length > 1 &&
            part &&
            typeof part === "object" &&
            (part.event || part.kind) ? (
              <details>
                <summary>
                  {fieldLabel(part.event || part.kind)}
                  {part.at || part.observed_at
                    ? ` · ${new Date(part.at || part.observed_at).toLocaleString()}`
                    : ""}
                </summary>
                <StructuredData value={part} />
              </details>
            ) : (
              <StructuredData value={part} />
            )}
          </div>
        ),
      )}
      {limit < parts.length && (
        <button onClick={() => setLimit((n) => n + 50)}>
          Show more activity · {parts.length - limit} remaining
        </button>
      )}
    </div>
  );
}
