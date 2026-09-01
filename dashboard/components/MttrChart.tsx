"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Summary } from "@/lib/api";

// The reconciliation cycle default (RECON_INTERVAL_SECONDS=60 in .env.example) —
// a documented system property, not an empirically measured latency. A live
// per-transaction MTTR figure was deliberately removed from /api/summary: it
// mixed real wall-clock timestamps with FakeTimeProvider-advanced ones from
// verification/eval scripts sharing the same tables, which produced numbers
// that looked measured but weren't. See docs/decisions.md ADR-025.
const RECON_CYCLE_MINUTES = 1;

export function MttrChart({ summary }: { summary: Summary | null }) {
  if (!summary) {
    return <div className="flex h-40 items-center justify-center text-sm text-text-tertiary">Loading…</div>;
  }

  const baselineMinutes = summary.baseline_resolution_days * 24 * 60;
  const data = [
    { name: "Manual escalation baseline", minutes: baselineMinutes, display: `${summary.baseline_resolution_days} days`, fill: "var(--accent-rose, #fb7185)" },
    { name: "Limbo (by design)", minutes: RECON_CYCLE_MINUTES, display: "~1 reconciliation cycle", fill: "#34d399" },
  ];

  return (
    <div>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 24, bottom: 0, left: 0 }}>
            <XAxis type="number" hide scale="log" domain={[1, "dataMax"]} />
            <YAxis
              type="category"
              dataKey="name"
              width={170}
              tick={{ fill: "var(--text-secondary)", fontSize: 12 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(255,255,255,0.03)" }}
              contentStyle={{
                background: "var(--surface-raised)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 12,
                color: "var(--text-primary)",
              }}
              formatter={(_value, _name, entry) => [entry.payload.display, "Resolution time"]}
            />
            <Bar dataKey="minutes" radius={[0, 4, 4, 0]} barSize={28}>
              {data.map((entry) => (
                <Cell key={entry.name} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[11px] text-text-tertiary">
        Log scale. Baseline is a documented assumption ({summary.baseline_resolution_days}-day manual escalation);
        Limbo&apos;s bound comes from the reconciliation interval, not an empirical average — see the note in the eval
        batch panel for why.
      </p>
    </div>
  );
}
