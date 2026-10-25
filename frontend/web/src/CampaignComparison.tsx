import { useState } from "react";
import ReadableTable from "./ReadableTable";

type Point = { date: string; growth: number; drawdown: number };
type Series = { name: string; color: string; points: Point[] };
type Comparison = { costs: Record<string, { series: Series[]; periods: Record<string, any>[]; months: Record<string, any>[] }>; limitations: string[] };
const pct = (value: number) => `${(value * 100).toFixed(2)}%`;

function Curve({ series, metric, title }: { series: Series[]; metric: "growth" | "drawdown"; title: string }) {
  const [index, setIndex] = useState<number | null>(null);
  const valid = series.filter(s => s.points.length > 1 && s.points.every(p => Number.isFinite(p[metric])));
  if (!valid.length) return null;
  const length = valid[0].points.length;
  if (valid.some(s => s.points.length !== length || s.points.some((p, i) => p.date !== valid[0].points[i].date))) return <p>Chart dates do not align.</p>;
  const values = valid.flatMap(s => s.points.map(p => p[metric]));
  const lo = Math.min(0, ...values), hi = metric === "drawdown" ? 0 : Math.max(0.01, ...values), range = Math.max(0.01, hi - lo);
  const x = (i: number) => 64 + i / (length - 1) * 718;
  const y = (v: number) => 22 + (hi - v) / range * 205;
  const selected = Math.max(0, Math.min(length - 1, index ?? length - 1));
  return <div className="analysis-line-chart">
    <h3>{title}</h3>
    <div className="analysis-chart-values"><time>{valid[0].points[selected].date}</time>{valid.map(s => <span key={s.name}><i style={{ background: s.color }} />{s.name}<strong>{pct(s.points[selected][metric])}</strong></span>)}</div>
    <svg viewBox="0 0 800 265" role="img" aria-label={`${title}. Left and right arrows inspect monthly observations.`} tabIndex={0}
      onKeyDown={e => { if (["ArrowLeft", "ArrowRight"].includes(e.key)) { e.preventDefault(); setIndex(Math.max(0, Math.min(length - 1, selected + (e.key === "ArrowLeft" ? -1 : 1)))); } }}
      onPointerMove={e => { const box = e.currentTarget.getBoundingClientRect(); setIndex(Math.max(0, Math.min(length - 1, Math.round(((e.clientX - box.left) / box.width * 800 - 64) / 718 * (length - 1))))); }} onPointerLeave={() => setIndex(null)}>
      <title>{title}</title>
      {[0, 1, 2, 3, 4].map(t => { const v = lo + range * t / 4; return <g key={t}><line x1="64" x2="782" y1={y(v)} y2={y(v)} stroke="currentColor" opacity=".12" /><text x="56" y={y(v) + 4} textAnchor="end" fill="currentColor" fontSize="12">{(Math.abs(v) < 0.00001 ? 0 : v * 100).toFixed(0)}%</text></g>; })}
      {valid.map(s => <path key={s.name} d={s.points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p[metric]).toFixed(2)}`).join(" ")} stroke={s.color} fill="none" strokeWidth="2" />)}
      <line x1={x(selected)} x2={x(selected)} y1="22" y2="227" stroke="currentColor" opacity=".35" />
      <text x="64" y="252" fill="currentColor" fontSize="12">{valid[0].points[0].date}</text><text x="782" y="252" textAnchor="end" fill="currentColor" fontSize="12">{valid[0].points[length - 1].date}</text>
    </svg>
  </div>;
}

export default function CampaignComparison({ value, cost }: { value: Comparison; cost: string }) {
  const selected = value.costs?.[cost];
  if (!selected) return null;
  return <div>
    <p>Each curve starts with $100,000 on January 3, 2017. The initial cash value is followed by monthly observations from a continuous daily account. QQQ is a reference investment with different concentration.</p>
    <Curve series={selected.series} metric="growth" title="Net investment growth" />
    <Curve series={selected.series} metric="drawdown" title="Distance below the previous account high" />
    <details><summary>Annual and stressed-market comparisons</summary><div className="table-scroll"><ReadableTable><thead><tr><th>Period</th><th>Strategy</th><th>Net return</th><th>Drawdown</th><th>Trading costs</th></tr></thead><tbody>{selected.periods.map((r, i) => <tr key={i}><td>{r.period}</td><td>{r.name}</td><td>{pct(r.net_return)}</td><td>{pct(r.max_drawdown)}</td><td>${Number(r.fees).toFixed(2)}</td></tr>)}</tbody></ReadableTable></div></details>
    <details><summary>All monthly returns and weak-month contributions</summary><div className="table-scroll"><ReadableTable><thead><tr><th>Month</th><th>Strategy</th><th>Return</th><th>Largest loss contributions</th></tr></thead><tbody>{selected.months.map((r, i) => <tr key={i}><td>{r.month}</td><td>{r.name}</td><td style={{ color: r.net_return < 0 ? "var(--danger, #f29999)" : undefined }}>{pct(r.net_return)}</td><td>{r.contributors || "—"}</td></tr>)}</tbody></ReadableTable></div></details>
    <details><summary>How to interpret these tests</summary><ul>{value.limitations.map((s, i) => <li key={i}>{s}</li>)}</ul></details>
  </div>;
}
