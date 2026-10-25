import ReadableTable from "./ReadableTable";
import { useEffect, useRef, useState } from "react";
import PaperSessionPanel from "./PaperSessionPanel";
import ResearchCampaignPanel from "./ResearchCampaignPanel";

type Obj = Record<string, any>;
type Request = (path: string) => Promise<Obj>;
type Point = { date: string; value: number };
type Series = { name: string; color: string; points: Point[] };
const retainedValidationIds = new Set(["core_daily", "baseline", "B1"]);
const colors = ["#2b63b8", "#91a0b5", "#32817d", "#9171b0"];
const finite = (v: any) => v !== null && v !== "" && Number.isFinite(Number(v));
const shortDate = (s: string) =>
  new Date(s + "T12:00:00Z").toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
const percentage = (n: number, digits = 1) =>
  (n > 0 ? "+" : "") + n.toFixed(digits) + "%";
const amount = (n: number) =>
  new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);

export function LineChart({
  series,
  label,
  percent = true,
  shade = false,
  bounds,
  signed = true,
}: {
  series: Series[];
  label: string;
  percent?: boolean;
  shade?: boolean;
  bounds?: [number, number];
  signed?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const valid = series.filter(
    (s) => s.points.length > 1 && s.points.every((p) => finite(p.value)),
  );
  const first = valid[0];
  if (!first)
    return (
      <div className="analysis-no-chart">
        At least two dated observations are needed to draw this chart.
      </div>
    );
  const W = 840,
    H = 285,
    L = 62,
    R = 18,
    T = 18,
    B = 38;
  const values = valid.flatMap((s) => s.points.map((p) => p.value));
  let lo = Math.min(...values, percent ? 0 : Infinity),
    hi = Math.max(...values, percent ? 0 : -Infinity);
  if (hi === lo) {
    lo -= 1;
    hi += 1;
  }
  if (bounds) {
    [lo, hi] = bounds;
  } else {
    const margin = (hi - lo) * 0.12;
    lo -= margin;
    hi += margin;
  }
  const x = (i: number) => L + (i / (first.points.length - 1)) * (W - L - R);
  const y = (v: number) => T + ((hi - v) / (hi - lo)) * (H - T - B);
  const path = (points: Point[]) =>
    points
      .map(
        (p, i) =>
          (i ? "L" : "M") + x(i).toFixed(2) + "," + y(p.value).toFixed(2),
      )
      .join(" ");
  const current =
    hover === null
      ? first.points.length - 1
      : Math.min(hover, first.points.length - 1);
  const spansYears =
    first.points[0].date.slice(0, 4) !==
    first.points[first.points.length - 1].date.slice(0, 4);
  const format = (n: number) =>
    percent ? (signed ? percentage(n) : n.toFixed(1) + "%") : amount(n);
  const select = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (box)
      setHover(
        Math.max(
          0,
          Math.min(
            first.points.length - 1,
            Math.round(
              ((((clientX - box.left) / box.width) * W - L) / (W - L - R)) *
                (first.points.length - 1),
            ),
          ),
        ),
      );
  };
  return (
    <div className="analysis-line-chart">
      <div className="analysis-chart-values" aria-live="polite">
        <time>{shortDate(first.points[current].date)}</time>
        {valid.map((s) => (
          <span key={s.name}>
            <i style={{ background: s.color }} />
            {s.name}
            <strong>{format(s.points[current]?.value ?? 0)}</strong>
          </span>
        ))}
      </div>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={
          label + ". Use the left and right arrow keys to inspect observations."
        }
        tabIndex={0}
        onPointerMove={(e) => select(e.clientX)}
        onPointerDown={(e) => select(e.clientX)}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            setHover(
              Math.max(
                0,
                Math.min(
                  first.points.length - 1,
                  current + (e.key === "ArrowLeft" ? -1 : 1),
                ),
              ),
            );
          }
        }}
      >
        <title>{label}</title>
        {[0, 1, 2, 3, 4].map((i) => {
          const value = lo + ((hi - lo) * i) / 4;
          return (
            <g key={i}>
              <line
                x1={L}
                x2={W - R}
                y1={y(value)}
                y2={y(value)}
                className="analysis-gridline"
              />
              <text
                x={L - 10}
                y={y(value) + 4}
                textAnchor="end"
                className="analysis-axis"
              >
                {percent
                  ? value.toFixed(0) + "%"
                  : Math.abs(value) >= 1000
                    ? (value / 1000).toFixed(0) + "k"
                    : value.toFixed(0)}
              </text>
            </g>
          );
        })}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const i = Math.round((first.points.length - 1) * f);
          return (
            <text
              key={f}
              x={x(i)}
              y={H - 10}
              textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}
              className="analysis-axis"
            >
              {spansYears
                ? shortDate(first.points[i].date)
                : shortDate(first.points[i].date).replace(/,? \d{4}/, "")}
            </text>
          );
        })}
        {shade && (
          <path
            d={
              path(first.points) +
              ` L${x(first.points.length - 1)},${y(0)} L${L},${y(0)} Z`
            }
            fill={first.color}
            opacity=".12"
          />
        )}
        {valid.map((s) => (
          <path
            key={s.name}
            d={path(s.points)}
            fill="none"
            stroke={s.color}
            strokeWidth={s === first ? 2.7 : 1.6}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {hover !== null && (
          <>
            <line
              x1={x(current)}
              x2={x(current)}
              y1={T}
              y2={H - B}
              stroke="#687b96"
              strokeDasharray="4 4"
            />
            {valid.map((s) => (
              <circle
                key={s.name}
                cx={x(current)}
                cy={y(s.points[current]?.value ?? 0)}
                r={4}
                fill={s.color}
                stroke="white"
                strokeWidth="2"
              />
            ))}
          </>
        )}
      </svg>
      <span className="analysis-chart-help">
        Move across the chart or use arrow keys to inspect values.
      </span>
    </div>
  );
}

function LossYearDiagnosis({ data }: { data: Obj }) {
  const max = Math.max(
    ...data.assets.map((a: Obj) => Math.abs(a.contribution)),
    0.01,
  );
  const costs = data.assets.reduce((n: number, a: Obj) => n + a.costs, 0);
  const cash =
    1 - data.assets.reduce((n: number, a: Obj) => n + a.average_weight, 0);
  return (
    <section className="loss-year-diagnosis" aria-label="2023 loss diagnosis">
      <h3>Why did 2023 lose money?</h3>
      <p>
        This diagnosis always uses native daily core at 0.06% trading cost per
        side, regardless of the chart settings. Silver trading was the largest
        loss contributor. Equity gains were too small to offset losses in
        silver, oil, bonds and other holdings.
      </p>
      <div className="analysis-metric-row">
        <div>
          <span>2023 return</span>
          <strong>{percentage(data.net_return * 100, 2)}</strong>
        </div>
        <div>
          <span>Trading costs included in result</span>
          <strong>{amount(costs)}</strong>
        </div>
        <div>
          <span>Average unused cash</span>
          <strong>{(cash * 100).toFixed(1)}%</strong>
        </div>
        <div>
          <span>Average QQQ allocation</span>
          <strong>
            {(
              data.assets.find((a: Obj) => a.symbol === "QQQ").average_weight *
              100
            ).toFixed(1)}
            %
          </strong>
        </div>
      </div>
      <h4>Contribution to the year’s return</h4>
      <p>
        One percentage point is 1% of the account’s starting-year value. These
        contributions already include trading fees and add up to the annual
        result. They are not the ETFs’ buy-and-hold returns.
      </p>
      <div className="diagnosis-bars">
        {[...data.assets]
          .sort((a: Obj, b: Obj) => a.contribution - b.contribution)
          .map((a: Obj) => (
            <div className="diagnosis-bar" key={a.symbol}>
              <span>{a.symbol}</span>
              <svg
                viewBox="0 0 400 24"
                role="img"
                aria-label={`${a.symbol} contribution ${percentage(a.contribution * 100, 2).replace("%", "")} percentage points`}
              >
                <line x1="200" x2="200" y1="0" y2="24" stroke="#aeb8c8" />
                <rect
                  x={
                    a.contribution < 0
                      ? 200 - (190 * Math.abs(a.contribution)) / max
                      : 200
                  }
                  y="4"
                  width={(190 * Math.abs(a.contribution)) / max}
                  height="16"
                  rx="3"
                  fill={a.contribution < 0 ? "#b4775e" : "#31877f"}
                />
              </svg>
              <strong>
                {percentage(a.contribution * 100, 2).replace("%", "")} pp
              </strong>
            </div>
          ))}
      </div>
      <p>
        QQQ rose {(data.benchmarks.QQQ * 100).toFixed(1)}% and SPY rose{" "}
        {(data.benchmarks.SPY * 100).toFixed(1)}% on the snapshot’s
        adjusted-price series. Core’s average QQQ weight was only{" "}
        {(
          data.assets.find((a: Obj) => a.symbol === "QQQ").average_weight * 100
        ).toFixed(1)}
        %. This points to allocation and timing, not simply a falling stock
        market.
      </p>
      {data.improved_variant && (
        <div className="diagnosis-improvement">
          <h4>What changed when all groups could compete?</h4>
          <p>
            The retained native variant returned{" "}
            {percentage(data.improved_variant.net_return * 100, 2)} in 2023 at
            the same cost assumption. QQQ’s average allocation rose from{" "}
            {(
              data.assets.find((a: Obj) => a.symbol === "QQQ").average_weight *
              100
            ).toFixed(1)}
            % to{" "}
            {(
              data.improved_variant.assets.find((a: Obj) => a.symbol === "QQQ")
                .average_weight * 100
            ).toFixed(1)}
            %. Silver still lost money; the improvement came from better
            allocation and timing across the portfolio.
          </p>
          <div className="analysis-table-wrap">
            <ReadableTable className="analysis-cost-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Original contribution</th>
                  <th>All groups contribution</th>
                  <th>Original allocation</th>
                  <th>All groups allocation</th>
                </tr>
              </thead>
              <tbody>
                {data.assets.map((a: Obj) => {
                  const b = data.improved_variant.assets.find(
                    (v: Obj) => v.symbol === a.symbol,
                  );
                  return (
                    <tr key={a.symbol}>
                      <td>{a.symbol}</td>
                      <td>
                        {percentage(a.contribution * 100, 2).replace("%", "")}{" "}
                        pp
                      </td>
                      <td>
                        {percentage(b.contribution * 100, 2).replace("%", "")}{" "}
                        pp
                      </td>
                      <td>{(a.average_weight * 100).toFixed(1)}%</td>
                      <td>{(b.average_weight * 100).toFixed(1)}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </ReadableTable>
          </div>
        </div>
      )}
      <p>
        The tests below change existing group-selection and trend rules across
        the full history. A positive result in an already studied year does not
        guarantee future losing years can be prevented.
      </p>
    </section>
  );
}

function NativeAnnualDetails({ data }: { data: Obj }) {
  const [cost, setCost] = useState("6");
  const [parent, setParent] = useState(data.parents?.[0]?.id || "current");
  const [view, setView] = useState("annual_portfolio");
  if (data.status !== "available") return <p>{data.message}</p>;
  const label = (id: string, fallback = id) =>
    data.yearly_gates &&
    (id.startsWith("cost_annual_") || id === "annual_portfolio")
      ? (
          id.startsWith("cost_annual_")
            ? id.includes("_252")
            : parent === "cost_annual_252"
        )
        ? "One-year cost-aware updates"
        : "Three-year cost-aware updates"
      : (
          {
            core_daily: "Current default",
            frontier_cap40: "Prior benchmark · smaller positions",
            frontier_band: "Prior benchmark · wider trade buffer",
            frontier_four_week: "Prior benchmark · four-week review",
            annual_portfolio: "Yearly allocation updates",
            annual_joint: "Combined yearly updates",
            annual_momentum: "Momentum recipe",
            annual_trend: "Trend recipe",
            annual_volatility: "Volatility recipe",
            annual_liquidity: "Liquidity recipe",
            annual_candle: "Price-pattern recipe",
            annual_gp: "Flexible pattern recipe",
          } as Record<string, string>
        )[id.split(":").pop() || id] || fallback;
  const reason = (value: string) =>
    value
      .replace(
        "full below historical frontier",
        "Full-history return trails a prior valid result",
      )
      .replace(
        "walk_forward below historical frontier",
        "2020-onward return trails a prior valid result",
      )
      .replace(
        "2023 below current default",
        "2023 return falls below the current default",
      )
      .replace(
        "drawdown worse than current default",
        "Largest fall is worse than the current default",
      );
  const scenario = data.scenarios[cost];
  const rows = scenario.rows.filter(
    (r: Obj) => r.parent === parent || r.parent === "reference",
  );
  const candidate = scenario.details[parent + ":" + view];
  return (
    <section
      className="native-annual-research"
      aria-label={data.region_label || "Native annual strategy research"}
    >
      <h3>
        {data.heading || "Can yearly updates improve the current strategy?"}
      </h3>
      <p>{data.message}</p>
      <p>
        Simulated $100,000 account · 2017–October 2026 · trading costs included.
      </p>
      <details>
        <summary>How the comparison was tested</summary>
        <p>{data.contract}</p>
      </details>
      <div className="analysis-metric-row">
        <div>
          <span>Default strategy</span>
          <strong>Unchanged</strong>
        </div>
        <div>
          <span>Annual variants tested</span>
          <strong>{data.candidate_count}</strong>
        </div>
        <div>
          <span>Passed every promotion check</span>
          <strong>{data.promoted_count}</strong>
        </div>
        <div>
          <span>Controls verified unchanged</span>
          <strong>{data.control_replays}</strong>
        </div>
      </div>
      <div className="analysis-controls">
        <label>
          Estimated buy/sell cost{" "}
          <select
            aria-label="Annual research trading cost"
            value={cost}
            onChange={(e) => setCost(e.target.value)}
          >
            {[2, 6, 10].map((v) => (
              <option key={v} value={v}>
                {v} bps
              </option>
            ))}
          </select>
        </label>
        <label>
          {data.parent_selector_label || "Starting strategy"}{" "}
          <select
            aria-label="Annual research starting strategy"
            value={parent}
            onChange={(e) => setParent(e.target.value)}
          >
            {(
              data.parents || [
                { id: "current", label: "Current default" },
                { id: "original", label: "Original design reference" },
              ]
            ).map((v: Obj) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          View a yearly strategy{" "}
          <select
            aria-label="Annual recipe details"
            value={view}
            onChange={(e) => setView(e.target.value)}
          >
            {data.families.map((v: Obj) => (
              <option key={v.id} value={v.id}>
                {label(v.id, v.label)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p>
        The selected cost is ${(Number(cost) / 100).toFixed(2)} for every $100
        bought or sold.
      </p>
      <div className="analysis-table-wrap">
        <ReadableTable aria-label="Annual strategy comparison">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>2017–2026 return</th>
              <th>2020 onward</th>
              <th>Largest fall</th>
              <th>2023</th>
              <th>Trading costs</th>
              <th>Decision</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r: Obj) => (
              <tr key={r.id}>
                <td title={r.label}>{label(r.id, r.label)}</td>
                <td>{percentage(r.full_return * 100, 2)}</td>
                <td>{percentage(r.forward_return * 100, 2)}</td>
                <td>{percentage(r.drawdown * 100, 2)}</td>
                <td>{percentage(r.return_2023 * 100, 2)}</td>
                <td>{amount(r.fees)}</td>
                <td title={reason(r.reason)}>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </ReadableTable>
      </div>
      <div aria-label="Annual strategy return bars">
        {rows.map((r: Obj) => (
          <div className="analysis-comparison-row" key={r.id}>
            <span>{label(r.id, r.label)}</span>
            <div className="analysis-comparison-track">
              <div
                style={{
                  width: `${(Math.max(0, r.full_return) / Math.max(...rows.map((v: Obj) => v.full_return))) * 100}%`,
                  background: r.parent === "reference" ? "#91a0b5" : "#32817d",
                  height: 12,
                }}
              />
            </div>
            <strong>{percentage(r.full_return * 100, 2)}</strong>
          </div>
        ))}
      </div>
      {candidate && (
        <>
          <h4>Account growth · {label(view, candidate.label)}</h4>
          <LineChart
            label="Native annual account growth"
            series={candidate.series.map((v: Obj, i: number) => ({
              ...v,
              name: data.parents
                ? v.name
                : i === 0
                  ? "Current default"
                  : label(view, candidate.label),
            }))}
            percent={false}
          />
          <div className="analysis-table-wrap">
            <ReadableTable aria-label="Native annual forward and stress results">
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Current default</th>
                  <th>Annual recipe</th>
                  <th>Difference</th>
                </tr>
              </thead>
              <tbody>
                {candidate.periods.map((r: Obj) => (
                  <tr key={r.period}>
                    <td>
                      {(
                        {
                          stress_2018: "2018 Q4",
                          stress_covid: "COVID selloff",
                          stress_2022: "2022 bear market",
                        } as Record<string, string>
                      )[r.period] || r.period}
                    </td>
                    <td>{percentage(r.baseline * 100, 2)}</td>
                    <td>{percentage(r.candidate * 100, 2)}</td>
                    <td>
                      {percentage((r.candidate - r.baseline) * 100, 2)} pts
                    </td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
          <h4>Month by month · {label(view, candidate.label)}</h4>
          <div className="analysis-month-grid">
            {candidate.monthly
              .filter((m: Obj) => m.month >= "2017-01")
              .map((m: Obj) => (
                <div
                  key={m.month}
                  className={
                    m.net_return < 0
                      ? "analysis-month loss"
                      : "analysis-month gain"
                  }
                  title={`Default: ${percentage(m.baseline * 100, 2)}`}
                >
                  <span>{m.month}</span>
                  <strong>{percentage(m.net_return * 100, 1)}</strong>
                </div>
              ))}
          </div>
          <p>{reason(candidate.reason)}</p>
        </>
      )}
      {data.uncertainty && (parent === "current" || data.parents) && (
        <>
          <h4>How certain are the apparent gains?</h4>
          <p>{data.uncertainty_explanation}</p>
          <div className="analysis-table-wrap">
            <ReadableTable aria-label="Native annual uncertainty">
              <thead>
                <tr>
                  <th>Recipe</th>
                  <th>Yearly relative growth</th>
                  <th>Adjusted uncertainty range</th>
                </tr>
              </thead>
              <tbody>
                {data.uncertainty
                  .filter(
                    (r: Obj) =>
                      String(r.cost_bps) === cost &&
                      (!data.parents || r.arm.startsWith(parent + ":")),
                  )
                  .map((r: Obj) => (
                    <tr key={r.arm}>
                      <td>{label(r.arm)}</td>
                      <td>{percentage(r.annual_relative_growth * 100, 2)}</td>
                      <td>
                        {percentage(r.intervals["20"].lower * 100, 2)} to{" "}
                        {percentage(r.intervals["20"].upper * 100, 2)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </ReadableTable>
          </div>
        </>
      )}
      {data.yearly_gates && (
        <>
          <h4>Which yearly updates were accepted?</h4>
          <p>
            Each decision uses only prices and execution outcomes available
            before that year. Rejected updates keep the previous settings.
          </p>
          <div className="analysis-table-wrap">
            <ReadableTable aria-label="Cost-aware yearly decisions">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Previous settings · past return</th>
                  <th>Proposed settings · past return</th>
                  <th>Cost buffer</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {data.yearly_gates[cost][parent].map((r: Obj) => (
                  <tr key={r.context_id}>
                    <td>{r.context_id.replace("outer-", "")}</td>
                    <td>{percentage(r.previous_net_return * 100, 2)}</td>
                    <td>{percentage(r.proposed_net_return * 100, 2)}</td>
                    <td>{percentage(r.incremental_cost_hurdle * 100, 2)}</td>
                    <td>
                      {r.accepted ? "Apply update" : "Keep previous settings"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
        </>
      )}
      <h4>What was verified?</h4>
      <ul>
        {data.validation.map((v: string) => (
          <li key={v}>{v}</li>
        ))}
      </ul>
      <p>{data.benchmark_review}</p>
      {data.benchmark_details?.status === "available" && (
        <>
          <h4>Benchmark review · completed measurements</h4>
          <div className="analysis-table-wrap">
            <ReadableTable aria-label="Native benchmark review">
              <thead>
                <tr>
                  <th>Evidence</th>
                  <th>Reviewed</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {data.benchmark_details.review_rows.map((r: Obj) => (
                  <tr key={r.scope}>
                    <td>{r.scope}</td>
                    <td>{r.count}</td>
                    <td>{r.result}</td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
          <div className="analysis-table-wrap">
            <ReadableTable aria-label="Expanded historical return frontier">
              <thead>
                <tr>
                  <th>Cost per side</th>
                  <th>Strongest ledger replay · full</th>
                  <th>Ledger replay · 2020 onward</th>
                  <th>Protected full-history frontier</th>
                  <th>Protected 2020-onward frontier</th>
                </tr>
              </thead>
              <tbody>
                {data.benchmark_details.frontier_rows.map((r: Obj) => (
                  <tr key={r.cost_bps}>
                    <td>{r.cost_bps} bps</td>
                    <td>{percentage(r.ledger_full * 100, 2)}</td>
                    <td>{percentage(r.ledger_forward * 100, 2)}</td>
                    <td>{percentage(r.protected_full * 100, 2)}</td>
                    <td>{percentage(r.protected_forward * 100, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
          <p>{data.benchmark_details.limitations}</p>
        </>
      )}
      {data.benchmark_details?.status === "unavailable" && (
        <p>{data.benchmark_details.message}</p>
      )}

      <p>{data.limitations}</p>
    </section>
  );
}

function EfficiencyDetails({ data, metric }: { data: Obj; metric: Obj }) {
  const base = data.baseline_metrics;
  const retained = data.approved.length - (data.reference_count || 1);
  return (
    <div className="validation-details">
      {data.native_annual?.cost_aware_comparison && (
        <NativeAnnualDetails data={data.native_annual.cost_aware_comparison} />
      )}
      {data.native_annual?.quality_comparison && (
        <NativeAnnualDetails data={data.native_annual.quality_comparison} />
      )}
      {data.native_annual && <NativeAnnualDetails data={data.native_annual} />}
      <h3>
        {data.native_annual
          ? "Earlier research campaign · archived comparison"
          : "Keep the improvements that earn their place"}
      </h3>
      <p>
        {data.campaign
          ? data.campaign.message
          : data.index_sleeves
            ? retained === 0
              ? "No index sleeve passed all return, drawdown and borrowing-cost checks. The existing strategies remain in place."
              : `${retained} index sleeves passed the fixed checks and remain available for research.`
            : retained === 0
              ? data.candidate_count && data.candidate_count !== 8
                ? `None of the ${data.candidate_count} changes passed every return and drawdown check. The selected overall default remains in place.`
                : "None of the eight changes passed every return and drawdown check. The selected overall default remains in place."
              : `${retained} changes passed the fixed checks and remain available for research. The selected overall default remains in place.`}
      </p>
      <p>
        Overall default:{" "}
        {data.current_default ||
          "All groups + volatility-triggered cost-aware switching"}
        .
      </p>
      {data.campaign && (
        <div className="analysis-metric-row">
          <div>
            <span>Cycle</span>
            <strong>{data.campaign.cycle_id}</strong>
          </div>
          <div>
            <span>Distinct verified histories</span>
            <strong>{data.campaign.history_count}</strong>
          </div>
          <div>
            <span>
              {data.campaign.campaign_final
                ? "Pending at campaign close"
                : "Earlier studies needing review"}
            </span>
            <strong>{data.campaign.pending_replay_count}</strong>
          </div>
          {data.campaign.verified_historical_replays !== undefined && (
            <div>
              <span>Historical strategies replayed</span>
              <strong>{data.campaign.verified_historical_replays}</strong>
            </div>
          )}
        </div>
      )}
      {data.campaign && (
        <div>
          <p>{data.campaign.hypothesis}</p>
          {data.campaign.outcome && <p>{data.campaign.outcome}</p>}
          {data.campaign.final_comparison && (
            <section aria-label="Final campaign comparison">
              <h4>Final comparison · 6 bps per side</h4>
              <p>
                All results use the same observed opening prices, whole-share
                purchases and trading limits. These are historical simulations.
                Stronger returns in one column alone do not qualify a strategy
                for promotion.
              </p>
              <div className="analysis-table-wrap">
                <ReadableTable
                  className="analysis-cost-table"
                  aria-label="Final strategy comparison"
                >
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Full history</th>
                      <th>2020 onward</th>
                      <th>Largest decline</th>
                      <th>2023</th>
                      <th>Decision</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.campaign.final_comparison.map((row: Obj) => (
                      <tr key={row.id}>
                        <td>{row.label}</td>
                        <td>{percentage(row.full_return * 100, 2)}</td>
                        <td>{percentage(row.forward_return * 100, 2)}</td>
                        <td>
                          {percentage(Math.abs(row.max_drawdown) * 100, 2)}
                        </td>
                        <td>{percentage(row.year2023 * 100, 2)}</td>
                        <td>{row.decision}</td>
                      </tr>
                    ))}
                  </tbody>
                </ReadableTable>
              </div>
              <div
                className="diagnosis-bars"
                aria-label="Final return comparison chart"
              >
                {data.campaign.final_comparison.map((row: Obj) => (
                  <div className="diagnosis-bar" key={row.id}>
                    <span>{row.label}</span>
                    <svg
                      viewBox="0 0 400 30"
                      role="img"
                      aria-label={`${row.label}: full history ${percentage(row.full_return * 100, 2)}, 2020 onward ${percentage(row.forward_return * 100, 2)}`}
                    >
                      <rect
                        x="0"
                        y="2"
                        width={Math.max(0, (row.full_return / 3.5) * 400)}
                        height="11"
                        fill="#4f85d5"
                      />
                      <rect
                        x="0"
                        y="17"
                        width={Math.max(0, (row.forward_return / 3.5) * 400)}
                        height="11"
                        fill="#62b394"
                      />
                    </svg>
                  </div>
                ))}
              </div>
              <p>
                Blue: full history. Green: 2020 onward. Both use the same scale;
                the table gives exact returns.
              </p>
            </section>
          )}
          {data.campaign.fold_validation && (
            <section aria-label="Training fold verification">
              <h4>Training checks completed</h4>
              <p>
                These checks establish reproducibility of archived fitted
                programs. They do not establish that those programs improve
                portfolio returns.
              </p>
              <div className="analysis-table-wrap">
                <ReadableTable
                  className="analysis-cost-table"
                  aria-label="Training fold validation results"
                >
                  <thead>
                    <tr>
                      <th>Archived fit</th>
                      <th>Original replay</th>
                      <th>Future-data test</th>
                      <th>Current libraries</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.campaign.fold_validation.map((row: Obj) => (
                      <tr key={row.id}>
                        <td>{row.label}</td>
                        <td>{row.original}</td>
                        <td>{row.future}</td>
                        <td>{row.current}</td>
                      </tr>
                    ))}
                  </tbody>
                </ReadableTable>
              </div>
              <p>{data.campaign.checkpoint_explanation}</p>
            </section>
          )}
          {data.campaign.pipeline_validation && (
            <section aria-label="Learned strategy validation">
              <h4>Can the learned strategy be used?</h4>
              <p>{data.campaign.execution_parity_explanation}</p>
              <div className="analysis-table-wrap">
                <ReadableTable
                  className="analysis-cost-table"
                  aria-label="Learned strategy validation gates"
                >
                  <thead>
                    <tr>
                      <th>Check</th>
                      <th>Result</th>
                      <th>What this establishes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.campaign.pipeline_validation.map((row: Obj) => (
                      <tr key={row.id}>
                        <td>{row.label}</td>
                        <td>{row.status}</td>
                        <td>{row.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </ReadableTable>
              </div>
            </section>
          )}
          {data.campaign.uncertainty_rows && (
            <section aria-label="Selection-adjusted uncertainty">
              <h4>Are the apparent gains reliable?</h4>
              <p>{data.campaign.uncertainty_explanation}</p>
              <div className="analysis-table-wrap">
                <ReadableTable
                  className="analysis-cost-table"
                  aria-label="Selection-adjusted growth intervals"
                >
                  <thead>
                    <tr>
                      <th>Strategy · 6 bps per side</th>
                      <th>Estimated yearly log-growth gap</th>
                      <th>95% interval · 1-month blocks</th>
                      <th>95% interval · 3-month blocks</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.campaign.uncertainty_rows.map((row: Obj) => (
                      <tr key={row.id}>
                        <td>{row.label}</td>
                        <td>
                          {percentage(
                            row.intervals["3"].annual_log_growth_difference *
                              100,
                            2,
                          ).replace("%", " pp")}
                        </td>
                        {["1", "3"].map((block) => (
                          <td key={block}>
                            {percentage(
                              row.intervals[block].simultaneous_lower * 100,
                              2,
                            ).replace("%", " pp")}{" "}
                            to{" "}
                            {percentage(
                              row.intervals[block].simultaneous_upper * 100,
                              2,
                            ).replace("%", " pp")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </ReadableTable>
              </div>
              <div
                className="diagnosis-bars"
                aria-label="Adjusted uncertainty interval chart"
              >
                {data.campaign.uncertainty_rows.map((row: Obj) => {
                  const interval = row.intervals["3"];
                  const limit = Math.max(
                    0.01,
                    ...data.campaign.uncertainty_rows.flatMap((item: Obj) => [
                      Math.abs(item.intervals["3"].simultaneous_lower),
                      Math.abs(item.intervals["3"].simultaneous_upper),
                    ]),
                  );
                  const position = (value: number) =>
                    200 + (value / limit) * 185;
                  return (
                    <div className="diagnosis-bar" key={row.id}>
                      <span>{row.label}</span>
                      <svg
                        viewBox="0 0 400 30"
                        role="img"
                        aria-label={`${row.label}: adjusted three-month-block interval includes zero`}
                      >
                        <line
                          x1="200"
                          x2="200"
                          y1="0"
                          y2="30"
                          stroke="#8b96a7"
                        />
                        <line
                          x1={position(interval.simultaneous_lower)}
                          x2={position(interval.simultaneous_upper)}
                          y1="15"
                          y2="15"
                          stroke="#4f85d5"
                          strokeWidth="4"
                        />
                        <circle
                          cx={position(interval.annual_log_growth_difference)}
                          cy="15"
                          r="5"
                          fill="#233b60"
                        />
                      </svg>
                    </div>
                  );
                })}
              </div>
              <p>
                Dot: estimated advantage. Line: adjusted three-month-block
                interval. Centre line: zero advantage. All rows share the same
                scale.
              </p>
            </section>
          )}
          <h4>Verified historical return frontier</h4>
          <p>
            The candidate must match or exceed these earlier results at every
            cost. Older incompatible studies remain under review, so this
            frontier is not yet the complete promotion benchmark.
          </p>
          <div className="analysis-table-wrap">
            <ReadableTable
              className="analysis-cost-table"
              aria-label="Historical return frontier"
            >
              <thead>
                <tr>
                  <th>Cost per side</th>
                  <th>Full history</th>
                  <th>2020 onward</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.campaign.frontier || {}).map(
                  ([cost, value]) => (
                    <tr key={cost}>
                      <td>{cost} bps</td>
                      <td>
                        {percentage((value as Obj).full.net_return * 100, 2)}
                      </td>
                      <td>
                        {percentage(
                          (value as Obj).walk_forward.net_return * 100,
                          2,
                        )}
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </ReadableTable>
          </div>
        </div>
      )}
      {data.campaign?.benchmark_replay?.rows && (
        <div>
          <h4>Earlier strategies retested fairly</h4>
          <p>
            These historical strategies now use the same data, trading costs and
            opening-volume limits. They are comparisons, not new strategy
            choices. Other historical strategies still need review.
          </p>
          <div className="analysis-table-wrap">
            <ReadableTable
              className="analysis-cost-table"
              aria-label="Historical strategy replays"
            >
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Cost per side</th>
                  <th>Full history</th>
                  <th>2020 onward</th>
                  <th>Largest decline</th>
                  <th>2023</th>
                </tr>
              </thead>
              <tbody>
                {data.campaign.benchmark_replay.rows.map((row: Obj) => (
                  <tr key={`${row.arm}-${row.cost_bps}`}>
                    <td>{row.label}</td>
                    <td>{row.cost_bps} bps</td>
                    <td>{percentage(row.full_return * 100, 2)}</td>
                    <td>{percentage(row.forward_return * 100, 2)}</td>
                    <td>{percentage(Math.abs(row.max_drawdown) * 100, 2)}</td>
                    <td>{percentage(row.year2023 * 100, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
        </div>
      )}
      {(data.sizing_conditions || data.campaign) && (
        <div
          className="analysis-comparison"
          aria-label="Conditional sizing results"
        >
          <h4>
            {data.campaign?.research_kind === "benchmark_replay" ||
            data.campaign?.research_kind === "benchmark_replay_revalidation"
              ? "How do the historical strategies compare?"
              : data.campaign
                ? "Did this campaign candidate improve performance?"
                : "Did conditional sizing improve returns?"}
          </h4>
          <p>
            All tested approaches at the selected period and cost. Removed
            experiments remain visible here for comparison.
          </p>
          {data.all_results.map((row: Obj) => (
            <div className="analysis-comparison-row" key={row.id}>
              <span>
                {row.name}
                <small>
                  {row.status === "incumbent" ? "Current best" : row.status}
                </small>
              </span>
              <span className="analysis-return-track">
                <i
                  style={{
                    width: `${Math.min(100, (Math.abs(row.net_return) / Math.max(...data.all_results.map((v: Obj) => Math.abs(v.net_return)), 0.001)) * 100)}%`,
                    background: row.id === "core_daily" ? "#7d9fcc" : "#b6a894",
                  }}
                />
                <b>{percentage(row.net_return * 100, 2)}</b>
              </span>
              <span>
                {Math.abs(row.max_drawdown * 100).toFixed(2)}% decline
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="analysis-metric-row">
        <div>
          <span>Changes retained</span>
          <strong>{retained}</strong>
        </div>
        <div>
          <span>Changes removed</span>
          <strong>{data.rejected_count}</strong>
        </div>
        <div>
          <span>Estimated trading costs</span>
          <strong>{amount(metric.costs)}</strong>
        </div>
        <div>
          <span>Trading volume / account per year</span>
          <strong>{metric.annualized_turnover.toFixed(1)}×</strong>
        </div>
      </div>
      <p>
        The comparison uses the same dates, starting cash, opening prices and
        execution limits. Costs are assumptions for spread, commission and
        impact. Trading volume counts both buys and sells.
      </p>
      {data.asset_sizing && (
        <div className="conditional-gate-chart">
          <h4>
            {data.percentile_sizing
              ? "How often was volatility unusually high?"
              : "When did each asset call for caution?"}
          </h4>
          <p>
            {data.percentile_sizing
              ? "A 90th-percentile trigger means volatility is above roughly nine out of ten observations from that asset’s prior year. The current observation is excluded from its threshold. The table shows trigger frequency across completed days, including days when the asset was not selected."
              : "Share of completed market days in the selected period. Each asset uses its own prices. Both sizing rules use the same trigger; only selected assets can have their allocation score changed."}
          </p>
          <div className="analysis-table-wrap">
            <ReadableTable
              className="analysis-cost-table"
              aria-label="Asset condition activation"
            >
              <thead>
                <tr>
                  <th>Asset</th>
                  {(data.asset_activation_columns || []).map((column: Obj) => (
                    <th key={column.key}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data.asset_activation || []).map((row: Obj) => (
                  <tr key={row.symbol}>
                    <td>{row.symbol}</td>
                    {(data.asset_activation_columns || []).map(
                      ({ key }: Obj) => (
                        <td
                          key={key}
                          style={{
                            background: `rgba(125,159,204,${0.06 + row[key] * 0.55})`,
                          }}
                        >
                          {(row[key] * 100).toFixed(1)}%
                        </td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
          <details>
            <summary>How asset sizing works</summary>
            {Object.values(data.condition_rules || {}).map((value, index) => (
              <p key={index}>{String(value)}</p>
            ))}
          </details>
        </div>
      )}
      <h3>Through difficult markets</h3>
      {(data.sizing_conditions || data.campaign) && (
        <p>
          All measured approaches are shown below for audit, including the
          removed experiments.
        </p>
      )}
      <div className="validation-stress-grid">
        {data.stress.map((window: Obj) => (
          <article key={window.label}>
            <h4>{window.label}</h4>
            {window.approaches.map((r: Obj) => (
              <div
                key={r.id}
                className={
                  "validation-stress-result " +
                  (r.net_return < 0 ? "loss" : "gain")
                }
              >
                <span>{r.name}</span>
                <strong>{percentage(r.net_return * 100, 2)}</strong>
                <meter
                  min={-30}
                  max={50}
                  value={r.net_return * 100}
                  aria-label={r.name + " growth in " + window.label}
                />
                <small>
                  {Math.abs(r.max_drawdown * 100).toFixed(2)}% largest decline
                </small>
              </div>
            ))}
          </article>
        ))}
      </div>
      <h3>Year-by-year forward evaluation</h3>
      <p>
        Rules were fixed before this experiment. These years carry the same
        account forward without annual resets. The history has been used in
        earlier research.
      </p>
      <div className="analysis-table-wrap">
        <ReadableTable className="analysis-cost-table">
          <thead>
            <tr>
              <th>Year</th>
              <th>Approach</th>
              <th>Growth</th>
              <th>Largest decline</th>
              <th>Filled trades</th>
              <th>Trading volume / account</th>
            </tr>
          </thead>
          <tbody>
            {data.yearly.flatMap((y: Obj) =>
              y.approaches.map((r: Obj) => (
                <tr key={y.year + r.id}>
                  <td>
                    {y.year}
                    {y.year === "2026" ? " (partial)" : ""}
                  </td>
                  <td>{r.name}</td>
                  <td>{percentage(r.net_return * 100, 2)}</td>
                  <td>{Math.abs(r.max_drawdown * 100).toFixed(2)}%</td>
                  <td>{r.fills}</td>
                  <td>{r.annualized_turnover.toFixed(1)}× annualized</td>
                </tr>
              )),
            )}
          </tbody>
        </ReadableTable>
      </div>
      <details className="analysis-assumptions">
        <summary>
          Rejected experiments ·{" "}
          {data.audit_label || "trade reduction and group-risk audit"}
        </summary>
        <p>
          {data.criterion} Rejected changes are removed from the active list.
          These measurements remain for audit.
        </p>
        <div className="analysis-table-wrap">
          <ReadableTable className="analysis-cost-table">
            <thead>
              <tr>
                <th>Experiment</th>
                <th>Decision</th>
                <th>Growth</th>
                <th>Largest decline</th>
                <th>Trades</th>
                <th>Trade-count change</th>
                <th>Trading costs</th>
                <th>Annual volume / account</th>
                {data.parent_metrics && <th>Change vs all-groups</th>}
              </tr>
            </thead>
            <tbody>
              {data.all_results.map((r: Obj) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td>{r.status}</td>
                  <td>{percentage(r.net_return * 100, 2)}</td>
                  <td>{Math.abs(r.max_drawdown * 100).toFixed(2)}%</td>
                  <td>{r.fills}</td>
                  <td>
                    {base.fills
                      ? percentage((r.fills / base.fills - 1) * 100, 1)
                      : "—"}
                  </td>
                  <td>{amount(r.costs)}</td>
                  <td>{r.annualized_turnover.toFixed(1)}×</td>
                  {data.parent_metrics && (
                    <td>
                      {percentage(
                        (r.net_return - data.parent_metrics.net_return) * 100,
                        2,
                      ).replace("%", "")}{" "}
                      pp
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </ReadableTable>
        </div>
        {data.condition_summary?.length > 0 && (
          <div className="conditional-gate-chart">
            <h4>How often did market conditions call for caution?</h4>
            <p>
              {data.group_sizing
                ? "Share of completed market days in the selected period when each group met its own condition. Only that group’s selected holdings are reweighted. Its combined allocation score is preserved before portfolio risk targeting and caps. Trades can occur from the following session."
                : data.sizing_conditions
                  ? "Share of completed market days in the selected period when each condition called for inverse-volatility sizing. The sizing change applies to all selected holdings and can trade from the following session. These signals use past prices only."
                  : "Share of asset-days after warmup meeting each condition. The cost hurdle applies only to an optional replacement when either asset meets the condition. These are historical signals, not next-day liquidity forecasts."}
            </p>
            {data.condition_summary.map((r: Obj) => (
              <div className="conditional-gate-row" key={r.id}>
                <span>{r.name}</span>
                <meter
                  min={0}
                  max={1}
                  value={r.fraction}
                  aria-label={`${r.name} activation share`}
                />
                <strong>{(r.fraction * 100).toFixed(1)}%</strong>
              </div>
            ))}
            <details>
              <summary>What triggers each condition?</summary>
              {Object.entries(data.condition_rules || {}).map(
                ([key, value]) => (
                  <p key={key}>{String(value)}</p>
                ),
              )}
            </details>
          </div>
        )}
        <h4>Return after each trading-cost assumption</h4>
        <div className="analysis-table-wrap">
          <ReadableTable className="analysis-cost-table">
            <thead>
              <tr>
                <th>Experiment</th>
                <th>0.02% per side</th>
                <th>0.06% per side</th>
                <th>0.10% per side</th>
              </tr>
            </thead>
            <tbody>
              {data.cost_grid.map((r: Obj) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  {[2, 6, 10].map((c) => (
                    <td key={c}>{percentage(r.values[String(c)] * 100, 2)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </ReadableTable>
        </div>
        {data.index_sleeves && (
          <div className="index-sleeve-audit">
            <h4>Does shorting still help after borrowing costs?</h4>
            <p>
              Full-history results at 0.06% trading cost per side. The borrowing
              scenarios assume 3% or 6% per year, or no shares available to
              borrow. These are stress assumptions; historical borrowing
              availability is unknown.
            </p>
            <div className="analysis-table-wrap">
              <ReadableTable className="analysis-cost-table">
                <thead>
                  <tr>
                    <th>Approach</th>
                    <th>3% borrowing</th>
                    <th>6% borrowing</th>
                    <th>No borrowing</th>
                    <th>Borrow fees</th>
                    <th>Days short</th>
                  </tr>
                </thead>
                <tbody>
                  {data.index_sleeves.borrow_rows.map((r: Obj) => (
                    <tr key={r.id}>
                      <td>{r.name}</td>
                      <td>{percentage(r.normal.net_return * 100, 2)}</td>
                      <td>{percentage(r.expensive.net_return * 100, 2)}</td>
                      <td>{percentage(r.unavailable.net_return * 100, 2)}</td>
                      <td>{amount(r.borrow_cost)}</td>
                      <td>{r.short_days}</td>
                    </tr>
                  ))}
                </tbody>
              </ReadableTable>
            </div>
            <details>
              <summary>Index sleeve design and limits</summary>
              <p>{data.index_sleeves.allocation}</p>
              <p>{data.index_sleeves.rules}</p>
              <p>{data.index_sleeves.cost_scope}</p>
            </details>
          </div>
        )}
        {data.preservation_2023 && (
          <div className="interaction-year-guard">
            <h4>Did we preserve the 2023 improvement?</h4>
            <p>
              This check always uses 0.06% per side. Both doubled-hurdle rules
              improved total returns but gave back part of the all-groups result
              in 2023, so the fixed preservation rule excludes them. This
              already-studied year is not an independent holdout.
            </p>
            <div className="analysis-table-wrap">
              <ReadableTable className="analysis-cost-table">
                <thead>
                  <tr>
                    <th>Approach</th>
                    <th>2023 return</th>
                    <th>Preservation check</th>
                  </tr>
                </thead>
                <tbody>
                  {data.preservation_2023.map((r: Obj) => (
                    <tr key={r.id}>
                      <td>{r.name}</td>
                      <td>{percentage(r.return * 100, 2)}</td>
                      <td>
                        {r.passes == null
                          ? "Reference"
                          : r.passes
                            ? "Preserved"
                            : "Below all-groups"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </ReadableTable>
            </div>
          </div>
        )}
        <h4>Uncertainty around the difference</h4>
        {data.uncertainty_reference && (
          <p>Comparison reference: {data.uncertainty_reference}.</p>
        )}
        <p>
          Full-history 0.06%-per-side comparison, regardless of the selected
          chart period. A paired three-month block bootstrap estimates the
          annual log-return difference. Intervals containing zero do not
          establish an improvement. This is descriptive evidence from previously
          examined history.
        </p>
        <div className="analysis-table-wrap">
          <ReadableTable className="analysis-cost-table">
            <thead>
              <tr>
                <th>Experiment</th>
                <th>95% interval · annual log-return difference</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.decisions)
                .filter(
                  ([, d]: [string, any]) => d.annualized_log_return_delta_ci95,
                )
                .map(([id, d]: [string, any]) => (
                  <tr key={id}>
                    <td>
                      {data.all_results.find((r: Obj) => r.id === id)?.name}
                    </td>
                    <td>
                      {d.annualized_log_return_delta_ci95
                        .map((n: number) => percentage(n * 100, 2))
                        .join(" to ")}
                    </td>
                  </tr>
                ))}
            </tbody>
          </ReadableTable>
        </div>
      </details>
    </div>
  );
}

function ValidationDetails({
  data,
  strategy,
  metric,
}: {
  data: Obj;
  strategy: string;
  metric: Obj;
}) {
  const [chosenMonth, setChosenMonth] = useState("");
  const months: Obj[] = data.months || [];
  const worst = [...months].sort(
    (a, b) =>
      a.approaches.find((r: Obj) => r.id === "core_daily").net_return -
      b.approaches.find((r: Obj) => r.id === "core_daily").net_return,
  )[0];
  const month = months.find((m) => m.month === chosenMonth) || worst;
  const select = (rows: Obj[]) => rows.find((r) => r.id === strategy)!;
  const signalSeries = [
    {
      name: "ML estimated risk",
      color: "#9171b0",
      points: data.signals.map((r: Obj) => ({
        date: r.date,
        value: r.probability * 100,
      })),
    },
  ];
  signalSeries.push({
    name: "Reduction threshold",
    color: "#b78b4d",
    points: data.signals.map((r: Obj) => ({ date: r.date, value: 35 })),
  });
  const quotaSeries = [
    {
      name: "Simple-rule recovery allowance",
      color: "#32817d",
      points: data.signals.map((r: Obj) => ({
        date: r.date,
        value: r.rule_quota * 100,
      })),
    },
    {
      name: "ML recovery allowance",
      color: "#9171b0",
      points: data.signals.map((r: Obj) => ({
        date: r.date,
        value: r.ml_quota * 100,
      })),
    },
  ];
  return (
    <div className="validation-details">
      <h3>What the tests tell us</h3>
      <div className="validation-findings">
        {data.findings.map((finding: string, i: number) => (
          <p key={i}>{finding}</p>
        ))}
      </div>
      <div className="analysis-metric-row">
        <div>
          <span>Estimated trading costs</span>
          <strong>{amount(metric.costs)}</strong>
        </div>
        <div>
          <span>Unfilled instructions</span>
          <strong>{metric.missed.toLocaleString()}</strong>
        </div>
        <div>
          <span>Annualized volatility</span>
          <strong>{(metric.annualized_volatility * 100).toFixed(1)}%</strong>
        </div>
      </div>
      <h3>What happened in difficult markets?</h3>
      <p>
        These named stress windows are fixed. The bars show growth; the decline
        is measured from the high within each window.
      </p>
      <div className="validation-stress-grid">
        {data.stress.map((s: Obj) => (
          <article key={s.period}>
            <h4>{s.label}</h4>
            {s.approaches
              .filter((r: Obj) =>
                ["core_daily", "baseline", "B1"].includes(r.id),
              )
              .map((r: Obj) => (
                <div
                  className={
                    "validation-stress-result " +
                    (r.net_return < 0 ? "loss" : "gain")
                  }
                  key={r.id}
                >
                  <span>{r.name}</span>
                  <strong
                    className={r.net_return < 0 ? "analysis-negative" : ""}
                  >
                    {percentage(r.net_return * 100, 2)}
                  </strong>
                  <meter
                    min="-30"
                    max="50"
                    value={r.net_return * 100}
                    aria-label={`${r.name} growth in ${s.label}`}
                  />
                  <small>
                    {Math.abs(r.max_drawdown * 100).toFixed(2)}% largest decline
                  </small>
                </div>
              ))}
          </article>
        ))}
      </div>
      <h3>Year-by-year walk-forward results</h3>
      <p>
        The ML indicator is trained before each year using earlier history.
        These are returns from a continuing account, with no annual cash reset.
        The dates have been used in earlier research.
      </p>
      <div className="analysis-table-wrap">
        <ReadableTable className="analysis-cost-table">
          <thead>
            <tr>
              <th>Test year</th>
              <th>Daily groups</th>
              <th>v13 reference</th>
              <th>Daily selection</th>
              <th>Selected approach decline</th>
            </tr>
          </thead>
          <tbody>
            {data.yearly.map((y: Obj) => (
              <tr key={y.year}>
                <td>
                  {y.year}
                  {y.year === "2026" ? " (partial)" : ""}
                </td>
                {["core_daily", "baseline", "B1"].map((id) => (
                  <td key={id}>
                    {percentage(
                      y.approaches.find((r: Obj) => r.id === id).net_return *
                        100,
                      2,
                    )}
                  </td>
                ))}
                <td>
                  {Math.abs(select(y.approaches).max_drawdown * 100).toFixed(2)}
                  %
                </td>
              </tr>
            ))}
          </tbody>
        </ReadableTable>
      </div>
      <h3>Find the difficult months</h3>
      <p>
        Select any month to compare its return, decline and cost with the
        reference strategies. The initial selection is the weakest daily-group
        month in the chosen period.
      </p>
      <div className="validation-month-buttons">
        {months.map((m: Obj) => {
          const r = select(m.approaches);
          return (
            <button
              key={m.month}
              aria-pressed={month?.month === m.month}
              onClick={() => setChosenMonth(m.month)}
              className={r.net_return < 0 ? "loss" : "gain"}
            >
              <span>{m.month}</span>
              <strong>{percentage(r.net_return * 100, 1)}</strong>
            </button>
          );
        })}
      </div>
      {month && (
        <>
          <h4>{month.month} · detailed comparison</h4>
          <p>
            {shortDate(month.start)}–{shortDate(month.end)}. Price moves over
            these dates provide market context; they are not the strategy’s
            holding contributions.
          </p>
          <div className="validation-market-context">
            {month.market_context.map((asset: Obj) => (
              <div
                key={asset.symbol}
                className={asset.return < 0 ? "loss" : "gain"}
              >
                <span>{asset.symbol}</span>
                <strong>{percentage(asset.return * 100, 1)}</strong>
              </div>
            ))}
          </div>
          <div className="analysis-table-wrap">
            <ReadableTable className="analysis-cost-table">
              <thead>
                <tr>
                  <th>Approach</th>
                  <th>Growth</th>
                  <th>Largest decline</th>
                  <th>Trading costs</th>
                  <th>Average invested</th>
                </tr>
              </thead>
              <tbody>
                {month.approaches
                  .filter((r: Obj) => retainedValidationIds.has(r.id))
                  .map((r: Obj) => (
                    <tr key={r.id}>
                      <td>{r.name}</td>
                      <td
                        className={r.net_return < 0 ? "analysis-negative" : ""}
                      >
                        {percentage(r.net_return * 100, 2)}
                      </td>
                      <td>{Math.abs(r.max_drawdown * 100).toFixed(2)}%</td>
                      <td>{amount(r.costs)}</td>
                      <td>{(r.average_exposure * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
              </tbody>
            </ReadableTable>
          </div>
        </>
      )}
      <details className="analysis-assumptions">
        <summary>Rejected experiments · recovery and ML audit</summary>
        <p>
          These candidates returned less than daily groups in both comparison
          periods at the registered primary cost. They have been removed from
          the strategy list. The evidence below is retained for audit only.
        </p>
        <h3>Recovery allowance</h3>
        <p>
          After a risk signal, the tested policy allows 25% of the strategy’s
          normal target exposure. It waits five healthy sessions, then adds 25
          percentage points after each three further healthy closes. A fresh
          risk signal resets the allowance. Actual invested exposure also
          depends on the strategy and available fills.
        </p>
        <LineChart
          series={quotaSeries}
          bounds={[0, 100]}
          signed={false}
          label="Allowed share of normal exposure during recovery, 2020 onward"
        />
        <details className="analysis-assumptions">
          <summary>Exit indicator accuracy and recovery history</summary>
          <h4>ML risk estimates · 2020 onward</h4>
          <p>
            The indicator estimates the chance that the ungated daily-group
            strategy loses at least 3% within the next ten closes. A 35%
            estimate reduces its target exposure to one quarter on the next
            executable session. It does not select individual assets or
            guarantee an exit price.
          </p>
          <LineChart
            series={signalSeries}
            bounds={[0, 100]}
            signed={false}
            label="Walk-forward ML adverse-condition probability"
          />
          <div className="analysis-table-wrap">
            <ReadableTable className="analysis-cost-table">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Last training outcome</th>
                  <th>First test date</th>
                  <th>Ranking score (AUC)</th>
                  <th>Probability error</th>
                  <th>Constant estimate error</th>
                  <th>False alarms / missed risks</th>
                </tr>
              </thead>
              <tbody>
                {data.folds.map((f: Obj) => (
                  <tr key={f.year}>
                    <td>{f.year}</td>
                    <td>{f.last_label_close}</td>
                    <td>{f.test_start}</td>
                    <td>{f.auc === null ? "Unavailable" : f.auc.toFixed(3)}</td>
                    <td>{f.brier.toFixed(3)}</td>
                    <td>{f.base_rate_brier.toFixed(3)}</td>
                    <td>
                      {f.confusion[0][1]} / {f.confusion[1][0]}
                    </td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
          <p>
            AUC measures ranking: 0.5 is chance. Lower probability error is
            better; the constant estimate uses the training period’s
            adverse-event rate. Counts are overlapping daily ten-session
            outcomes, not independent events.
          </p>
          <h4>Deepest declines and time to regain the previous high</h4>
          <p>
            Selected approach, full 2017–2026 account history, regardless of the
            chart period. Elapsed trading sessions are measured from the first
            close below the previous high. An unrecovered episode measures
            elapsed time to the dataset end.
          </p>
          <div className="analysis-table-wrap">
            <ReadableTable className="analysis-cost-table">
              <thead>
                <tr>
                  <th>Previous high</th>
                  <th>Low point</th>
                  <th>Decline</th>
                  <th>Recovered</th>
                  <th>Sessions below high</th>
                </tr>
              </thead>
              <tbody>
                {data.recovery_episodes.map((e: Obj) => (
                  <tr key={e.peak}>
                    <td>{e.peak}</td>
                    <td>{e.trough}</td>
                    <td>{Math.abs(e.drawdown * 100).toFixed(2)}%</td>
                    <td>{e.recovered || "Still recovering"}</td>
                    <td>{e.sessions}</td>
                  </tr>
                ))}
              </tbody>
            </ReadableTable>
          </div>
        </details>
      </details>
    </div>
  );
}

export function PerformanceCharts({ request }: { request: Request }) {
  const [period, setPeriod] = useState("walk_forward");
  const [study, setStudy] = useState("campaign");
  const [strategy, setStrategy] = useState("core_daily");
  const [cost, setCost] = useState(6);
  const [benchmarks, setBenchmarks] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision((n) => n + 1);
    window.addEventListener("amat:refresh", refresh);
    return () => window.removeEventListener("amat:refresh", refresh);
  }, []);
  const [data, setData] = useState<Obj | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    request(
      `/analysis/performance?period=${period}&strategy=${strategy}&cost=${cost}&study=${study}`,
    )
      .then((x) => {
        if (live) setData(x);
      })
      .catch(() => {
        if (live) setError("We couldn’t load the saved performance charts.");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [period, strategy, cost, study, request, revision]);
  const metric = data?.metrics;
  const comparison = (data?.comparison || []).filter(
    (r: Obj) => study !== "validation" || retainedValidationIds.has(r.id),
  );
  const costSensitivity = (data?.cost_sensitivity || []).filter(
    (r: Obj) => study !== "validation" || retainedValidationIds.has(r.id),
  );
  const series: Series[] = (data?.series || []).map((s: Obj, i: number) => ({
    name: s.name,
    color:
      i === 0
        ? colors[0]
        : s.id === "B1"
          ? "#b78b4d"
          : s.id === "conditional_reference"
            ? "#b78b4d"
            : s.id === "baseline" || s.id === "all_groups"
              ? colors[1]
              : s.id === "groups"
                ? "#b66f8e"
                : s.id === "core_daily"
                  ? "#b66f8e"
                  : colors[i % colors.length],
    points: s.points.map((p: Obj) => ({ date: p.date, value: p.growth })),
  }));
  if (benchmarks)
    (data?.benchmarks || []).forEach((s: Obj, i: number) =>
      series.push({
        name: s.name,
        color: colors[i + 2],
        points: s.points.map((p: Obj) => ({ date: p.date, value: p.growth })),
      }),
    );
  const drawdown: Series[] = (data?.series || []).slice(0, 1).map((s: Obj) => ({
    name: s.name,
    color: "#af694f",
    points: s.points.map((p: Obj) => ({ date: p.date, value: p.drawdown })),
  }));
  const maxReturn = Math.max(
    1,
    ...comparison.map((r: Obj) => Math.abs(Number(r.net_return)) * 100),
  );
  return (
    <section className="analysis-workspace" aria-label="Strategy analysis">
      <PaperSessionPanel request={request} />
      <ResearchCampaignPanel request={request} />
      <div className="analysis-section-title">
        <div>
          <span className="analysis-kicker">HISTORICAL STRATEGY TESTS</span>
          <h2>
            {study === "campaign"
              ? "What is the research campaign learning?"
              : study === "percentile_sizing"
                ? "Does unusually high volatility call for smaller positions?"
                : study === "asset_sizing"
                  ? "Can each asset signal when to size defensively?"
                  : study === "group_sizing"
                    ? "Does each group know when to size defensively?"
                    : study === "conditional_sizing"
                      ? "Can defensive sizing help only when conditions weaken?"
                      : study === "volatility_next"
                        ? "Five ways to improve the current best"
                        : study === "interaction"
                          ? "Do the two improvements work better together?"
                          : study === "recovery" || study === "quality_recovery"
                            ? "What went wrong in 2023—and what helps?"
                            : study === "opportunistic"
                              ? "Can unused cash work harder?"
                              : study === "indices"
                                ? "Do tailored index strategies add value?"
                                : study === "conditional"
                                  ? "When does smarter switching help?"
                                  : study === "switching"
                                    ? "Does waiting for a better switch pay off?"
                                    : study === "efficiency"
                                      ? "Can we trade less and protect the right groups?"
                                      : study === "validation"
                                        ? "How resilient are daily groups?"
                                        : study === "groups"
                                          ? "What improves group selection?"
                                          : study === "adaptive"
                                            ? "Does adaptive rotation improve the results?"
                                            : "How has the strategy performed?"}
          </h2>
          <p>Compare growth and declines across past market conditions.</p>
        </div>
        <span className="analysis-data-badge">Simulated results</span>
      </div>
      <div className="analysis-controls">
        <label>
          Research comparison
          <select
            aria-label="Research comparison"
            value={study}
            onChange={(e) => {
              setStudy(e.target.value);
              setCost(
                [
                  "recovery",
                  "quality_recovery",
                  "interaction",
                  "volatility_next",
                  "conditional_sizing",
                  "group_sizing",
                  "asset_sizing",
                  "percentile_sizing",
                  "campaign",
                ].includes(e.target.value)
                  ? 6
                  : 2,
              );
              setPeriod(
                [
                  "indices",
                  "opportunistic",
                  "recovery",
                  "quality_recovery",
                  "interaction",
                  "volatility_next",
                  "conditional_sizing",
                  "group_sizing",
                  "asset_sizing",
                  "percentile_sizing",
                  "campaign",
                  "conditional",
                  "switching",
                  "efficiency",
                  "validation",
                ].includes(e.target.value)
                  ? "walk_forward"
                  : e.target.value === "groups"
                    ? "full"
                    : "2026_ytd",
              );
              setStrategy(
                e.target.value === "interaction"
                  ? "all_volatility"
                  : [
                        "indices",
                        "opportunistic",
                        "recovery",
                        "quality_recovery",
                        "interaction",
                        "volatility_next",
                        "conditional_sizing",
                        "group_sizing",
                        "asset_sizing",
                        "percentile_sizing",
                        "campaign",
                        "conditional",
                        "switching",
                        "efficiency",
                        "validation",
                      ].includes(e.target.value)
                    ? "core_daily"
                    : e.target.value === "groups"
                      ? "wide_both"
                      : e.target.value === "adaptive"
                        ? "combined"
                        : "baseline",
              );
              setData(null);
            }}
          >
            <option value="campaign">
              Research campaign · latest completed cycle
            </option>
            <option value="percentile_sizing">
              Earlier research · asset volatility percentiles
            </option>
            <option value="asset_sizing">
              Earlier research · asset-specific defensive sizing
            </option>
            <option value="group_sizing">
              Earlier research · group-specific defensive sizing
            </option>
            <option value="conditional_sizing">
              Current best · defensive sizing by conditions
            </option>
            <option value="volatility_next">
              Current best · five improvement tests
            </option>
            <option value="interaction">
              All groups · conditional switching combinations
            </option>
            <option value="recovery">
              2023 investigation · native group improvements
            </option>
            <option value="quality_recovery">
              2023 investigation · trend quality and lag
            </option>
            <option value="opportunistic">
              Unused cash · portfolio-netted index sleeves
            </option>
            <option value="indices">
              Core groups · tailored long/short index sleeves
            </option>
            <option value="conditional">
              Daily core groups · switching by market conditions
            </option>
            <option value="switching">
              Daily core groups · smarter switching
            </option>
            <option value="efficiency">
              Daily core groups · fewer trades & group risk
            </option>
            <option value="validation">
              Daily groups · long history & exit tests
            </option>
            <option value="groups">Archived · broader ETF research</option>
            <option value="adaptive">Archived · adaptive rotation tests</option>
            <option value="reference">Archived · earlier comparisons</option>
          </select>
        </label>
        <label>
          Time period
          <select
            aria-label="Time period"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
          >
            {[
              "validation",
              "efficiency",
              "switching",
              "conditional",
              "indices",
              "opportunistic",
              "recovery",
              "quality_recovery",
              "interaction",
              "volatility_next",
              "conditional_sizing",
              "group_sizing",
              "asset_sizing",
              "percentile_sizing",
              "campaign",
            ].includes(study) && (
              <>
                <option value="walk_forward">
                  Walk-forward comparison · 2020 onward
                </option>
                <option value="stress_2018">2018 fourth-quarter selloff</option>
                <option value="stress_covid">
                  COVID crash and initial rebound
                </option>
                <option value="stress_2022">2022 bear market</option>
                {[2017, 2018, 2019, 2020, 2021, 2022, 2023].map((y) => (
                  <option key={y} value={String(y)}>
                    {y}
                  </option>
                ))}
              </>
            )}
            <option value="2026_ytd">2026 to date</option>
            <option value="2025">2025</option>
            <option value="2024">2024</option>
            <option value="recent">July–October 2026</option>
            <option value="full">
              {study === "groups"
                ? "Full comparison · 2024 onward"
                : "Full available history"}
            </option>
          </select>
        </label>
        <label>
          Strategy
          <select
            aria-label="Strategy"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            {(comparison.length
              ? comparison
              : [
                  {
                    id: strategy,
                    name:
                      study === "adaptive"
                        ? "Adaptive rotation combined"
                        : "Current reference",
                  },
                ]
            ).map((row: Obj) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <label className="analysis-checkbox">
          <input
            type="checkbox"
            checked={benchmarks}
            onChange={(e) => setBenchmarks(e.target.checked)}
          />
          Compare with SPY and QQQ
        </label>
      </div>
      {loading ? (
        <div className="analysis-loading" role="status">
          Loading measured results…
        </div>
      ) : error || data?.status !== "available" ? (
        <p className="analysis-alert">
          {error ||
            data?.reason ||
            "No saved performance results are available yet."}
        </p>
      ) : (
        <>
          <p className="analysis-source-label">
            {shortDate(data.start)}–{shortDate(data.end)} · Includes{" "}
            {(cost / 100).toFixed(2)}% trading cost per side · Research results,
            separate from your account
          </p>
          {data.comparison_scope && (
            <p className="analysis-source-label">
              All approaches start with $100,000 in cash in January 2024.
              Earlier prices are used for signals only.
            </p>
          )}
          <div className="analysis-metric-row">
            <div>
              <span>Total growth</span>
              <strong
                className={metric.net_return < 0 ? "analysis-negative" : ""}
              >
                {percentage(metric.net_return * 100, 2)}
              </strong>
            </div>
            <div>
              <span>Largest decline</span>
              <strong>{Math.abs(metric.max_drawdown * 100).toFixed(2)}%</strong>
            </div>
            <div>
              <span>Trades filled</span>
              <strong>{metric.fills.toLocaleString()}</strong>
            </div>
            <div>
              <span>Average invested</span>
              <strong>{(metric.average_exposure * 100).toFixed(0)}%</strong>
            </div>
          </div>
          <div className="analysis-primary-plot">
            <h3>Growth over time</h3>
            <p>
              All lines start at the same value. Benchmarks exclude trading
              costs.
            </p>
            <LineChart
              series={series}
              label="Cumulative strategy growth and buy-and-hold benchmark comparison"
            />
          </div>
          <div className="analysis-two-plots">
            <div>
              <h3>Declines from a previous high</h3>
              <p>
                Lower values mean a deeper decline during the selected period.
              </p>
              <LineChart
                series={drawdown}
                label="Strategy decline from its running high"
                shade
              />
            </div>
            <div className="analysis-monthly">
              <h3>Month by month</h3>
              <p>Growth within the selected dates of each month.</p>
              <div className="analysis-month-grid">
                {data.monthly.map((m: Obj) => (
                  <div
                    className={
                      m.return < 0
                        ? "analysis-month loss"
                        : "analysis-month gain"
                    }
                    key={m.month}
                  >
                    <span>
                      {new Date(m.month + "-15T12:00:00Z").toLocaleDateString(
                        undefined,
                        { month: "short", year: "2-digit", timeZone: "UTC" },
                      )}
                    </span>
                    <strong>{percentage(m.return)}</strong>
                  </div>
                ))}
              </div>
              <small>
                First and last months may be partial. Hover over the growth
                chart for exact dates.
              </small>
            </div>
          </div>
          {data.diagnostic_2023 && (
            <LossYearDiagnosis data={data.diagnostic_2023} />
          )}
          {data.efficiency && (
            <EfficiencyDetails data={data.efficiency} metric={metric} />
          )}
          {data.validation && (
            <ValidationDetails
              data={data.validation}
              strategy={strategy}
              metric={metric}
            />
          )}
          {data.factorial?.length > 0 && (
            <div className="analysis-factorial">
              <h3>Which combination helps?</h3>
              <p>
                Same starting cash and period. Select a result to inspect its
                growth and declines.
              </p>
              <div className="analysis-factor-row heading">
                <span>Group-selection settings</span>
                <span>11 core ETFs</span>
                <span>38 broader ETFs</span>
              </div>
              {data.factorial.map((row: Obj) => (
                <div className="analysis-factor-row" key={row.label}>
                  <span>{row.label}</span>
                  {["core", "wide"].map((key) => (
                    <button
                      key={key}
                      aria-pressed={strategy === row[key].id}
                      onClick={() => setStrategy(row[key].id)}
                    >
                      <strong>
                        {percentage(row[key].net_return * 100, 2)}
                      </strong>
                      <small>
                        {Math.abs(row[key].max_drawdown * 100).toFixed(2)}%
                        largest decline
                      </small>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
          <div className="analysis-comparison">
            <div>
              <h3>Compare the tested approaches</h3>
              <p>
                Past tests show trade-offs. A better past result does not
                establish a better future strategy.
              </p>
            </div>
            <div className="analysis-comparison-head">
              <span>Strategy</span>
              <span>Growth</span>
              <span>Largest decline</span>
            </div>
            {comparison.map((r: Obj) => (
              <button
                key={r.id}
                className={
                  "analysis-comparison-row " +
                  (strategy === r.id ? "chosen" : "")
                }
                onClick={() => setStrategy(r.id)}
              >
                <span>
                  {r.name}
                  {study === "interaction" && r.id === "all_volatility" && (
                    <small>Current best · overall default</small>
                  )}
                  {r.id === "baseline" && <small>Reference control</small>}
                  {[
                    "validation",
                    "efficiency",
                    "switching",
                    "conditional",
                    "indices",
                    "opportunistic",
                    "recovery",
                    "quality_recovery",
                    "interaction",
                    "volatility_next",
                    "conditional_sizing",
                    "group_sizing",
                    "asset_sizing",
                    "percentile_sizing",
                    "campaign",
                  ].includes(study) &&
                    r.id === "core_daily" && (
                      <small>
                        {study === "indices"
                          ? "Do tailored index strategies add value?"
                          : study === "conditional"
                            ? "Default reference"
                            : [
                                  "volatility_next",
                                  "conditional_sizing",
                                  "group_sizing",
                                  "asset_sizing",
                                  "percentile_sizing",
                                  "campaign",
                                ].includes(study)
                              ? "Current best"
                              : "Historical reference"}
                      </small>
                    )}
                </span>
                <span className="analysis-return-track">
                  <i
                    style={{
                      width:
                        (Math.abs(r.net_return * 100) / maxReturn) * 100 + "%",
                      background: r.net_return < 0 ? "#b4775e" : "#7d9fcc",
                    }}
                  />
                  <b>{percentage(r.net_return * 100, 2)}</b>
                </span>
                <span>{Math.abs(r.max_drawdown * 100).toFixed(2)}%</span>
              </button>
            ))}
          </div>
          {data.cost_sensitivity?.length > 0 && (
            <div className="analysis-comparison">
              <h3>How much do trading costs change the result?</h3>
              <p>
                Same period and fixed rules at three costs per side. Current and
                reference approaches are shown; earlier studies are archived.
              </p>
              <div className="analysis-table-wrap">
                <ReadableTable className="analysis-cost-table">
                  <thead>
                    <tr>
                      <th>Approach</th>
                      <th>0.02%</th>
                      <th>0.06%</th>
                      <th>0.10%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {costSensitivity.map((row: Obj) => (
                      <tr key={row.id}>
                        <td>{row.name}</td>
                        {[2, 6, 10].map((fee) => (
                          <td
                            key={fee}
                            className={
                              row.values[String(fee)] < 0
                                ? "analysis-negative"
                                : ""
                            }
                          >
                            {percentage(row.values[String(fee)] * 100, 2)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </ReadableTable>
              </div>
            </div>
          )}
          <details
            className="analysis-assumptions"
            open={settingsOpen}
            onToggle={(e) => setSettingsOpen(e.currentTarget.open)}
          >
            <summary>Comparison settings and source</summary>
            <label>
              Assumed trading cost per side
              <select
                value={cost}
                onChange={(e) => setCost(Number(e.target.value))}
              >
                <option value={2}>0.02% per trade</option>
                <option value={6}>0.06% per trade</option>
                <option value={10}>0.10% per trade</option>
              </select>
            </label>
            <p>
              {data.quantity_scope} {data.scope} {data.benchmark_scope}{" "}
              {data.study_scope} {data.execution_scope} {data.weekly_scope}{" "}
              {data.comparison_scope}
            </p>
            <small>{data.source}</small>
          </details>
        </>
      )}
    </section>
  );
}
