"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { LimboQueueItem } from "@/lib/api";
import { stateMeta } from "@/lib/theme";

export function QueueByStateChart({ items }: { items: LimboQueueItem[] }) {
  const counts = new Map<string, number>();
  for (const item of items) {
    counts.set(item.state, (counts.get(item.state) ?? 0) + 1);
  }
  const data = Array.from(counts.entries()).map(([state, count]) => ({
    state,
    count,
    ...stateMeta(state),
  }));

  if (data.length === 0) {
    return <div className="flex h-56 items-center justify-center text-sm text-text-tertiary">Queue is empty.</div>;
  }

  return (
    <div className="flex items-center gap-4">
      <div className="h-56 w-56 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="count"
              nameKey="label"
              innerRadius={55}
              outerRadius={85}
              paddingAngle={3}
              stroke="none"
            >
              {data.map((entry) => (
                <Cell key={entry.state} fill={entry.hex} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                background: "var(--surface-raised)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 12,
                color: "var(--text-primary)",
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="space-y-2 text-sm">
        {data
          .sort((a, b) => b.count - a.count)
          .map((entry) => (
            <li key={entry.state} className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: entry.hex }} />
              <span className="text-text-secondary">{entry.label}</span>
              <span className="ml-auto font-medium tabular-nums text-text-primary">{entry.count}</span>
            </li>
          ))}
      </ul>
    </div>
  );
}
