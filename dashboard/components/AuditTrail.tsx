"use client";

import { useEffect, useRef, useState } from "react";
import { AuditLogItem } from "@/lib/api";

interface DisplayRow {
  key: string;
  stage: string;
  event: string;
  order_id: string | null;
  reasoning: string | null;
  created_at: string;
}

// Every idempotency-guardrail skip writes two audit rows: the "decision" that
// re-selected the same action, and the "action" row recording the skip. They
// are the same real-world event (a re-run that correctly did nothing), so
// they are combined into one displayed row rather than shown as two.
function collapseRows(items: AuditLogItem[]): DisplayRow[] {
  const rows: DisplayRow[] = [];
  for (let i = 0; i < items.length; i++) {
    const cur = items[i];
    const next = items[i + 1];
    if (cur.event === "SKIPPED_IDEMPOTENT" && next && next.stage === "decision" && next.order_id === cur.order_id) {
      rows.push({
        key: `${cur.created_at}-${cur.order_id}-${i}`,
        stage: "idempotency guard",
        event: `${next.event} → SKIPPED_IDEMPOTENT (already executed)`,
        order_id: cur.order_id,
        reasoning: cur.reasoning,
        created_at: cur.created_at,
      });
      i += 1;
      continue;
    }
    rows.push({
      key: `${cur.created_at}-${cur.order_id}-${i}`,
      stage: cur.stage,
      event: cur.event,
      order_id: cur.order_id,
      reasoning: cur.reasoning,
      created_at: cur.created_at,
    });
  }
  return rows;
}

const STAGE_COLORS: Record<string, string> = {
  detection: "text-text-secondary",
  decision: "text-risk",
  action: "text-money",
  outcome: "text-money",
  diagnosis: "text-dup",
  "idempotency guard": "text-text-tertiary",
};

export function AuditTrail({ items }: { items: AuditLogItem[] }) {
  const rows = collapseRows(items);
  const seenKeys = useRef<Set<string>>(new Set());
  const [freshKeys, setFreshKeys] = useState<Set<string>>(new Set());

  useEffect(() => {
    const fresh = new Set<string>();
    for (const row of rows) {
      if (!seenKeys.current.has(row.key)) fresh.add(row.key);
    }
    if (fresh.size > 0 && seenKeys.current.size > 0) {
      setFreshKeys(fresh);
      const t = setTimeout(() => setFreshKeys(new Set()), 900);
      rows.forEach((r) => seenKeys.current.add(r.key));
      return () => clearTimeout(t);
    }
    rows.forEach((r) => seenKeys.current.add(r.key));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  if (rows.length === 0) {
    return <div className="py-8 text-center text-sm text-text-tertiary">No audit entries yet.</div>;
  }

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-[11px] text-text-tertiary">
        <span className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-money" />
        live — polling every 5s
      </div>
      <div className="max-h-80 w-full overflow-y-auto">
        <table className="w-full table-auto text-left text-sm">
          <thead className="sticky top-0 bg-surface">
            <tr className="border-b border-border-subtle text-[11px] uppercase tracking-wide text-text-tertiary">
              <th className="px-3 py-2 font-medium">Stage</th>
              <th className="px-3 py-2 font-medium">Event</th>
              <th className="px-3 py-2 font-medium">Order</th>
              <th className="px-3 py-2 font-medium">Reasoning</th>
              <th className="px-3 py-2 font-medium">When</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className={`border-b border-border-subtle last:border-0 ${freshKeys.has(row.key) ? "audit-row-enter" : ""}`}
              >
                <td className={`px-3 py-2 text-xs font-medium ${STAGE_COLORS[row.stage] ?? "text-text-secondary"}`}>{row.stage}</td>
                <td className="px-3 py-2 text-text-primary">{row.event}</td>
                <td className="px-3 py-2 font-mono text-xs text-text-secondary">{row.order_id ?? "—"}</td>
                <td className="px-3 py-2 text-text-tertiary">{row.reasoning ?? "—"}</td>
                <td className="px-3 py-2 text-xs text-text-tertiary">{new Date(row.created_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
