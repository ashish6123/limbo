"use client";

import { useMemo, useState } from "react";
import { LimboQueueItem, formatRupees } from "@/lib/api";
import { stateMeta } from "@/lib/theme";

const PAGE_SIZE = 10;

type SortKey = "order_id" | "state" | "amount_paise" | "classified_at";
type SortDir = "asc" | "desc";

export function LimboQueueTable({ items }: { items: LimboQueueItem[] }) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("classified_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = q
      ? items.filter((i) => i.order_id.toLowerCase().includes(q) || i.state.toLowerCase().includes(q))
      : items;

    const sorted = [...base].sort((a, b) => {
      const dir = sortDir === "asc" ? 1 : -1;
      if (sortKey === "amount_paise") return (a.amount_paise - b.amount_paise) * dir;
      if (sortKey === "classified_at") return (Date.parse(a.classified_at) - Date.parse(b.classified_at)) * dir;
      return a[sortKey].localeCompare(b[sortKey]) * dir;
    });
    return sorted;
  }, [items, query, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageItems = filtered.slice(clampedPage * PAGE_SIZE, clampedPage * PAGE_SIZE + PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
    setPage(0);
  }

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(0);
          }}
          placeholder="Search order id or state…"
          className="w-full max-w-xs rounded-md border border-border bg-surface-raised px-3 py-1.5 text-sm text-text-primary placeholder:text-text-tertiary focus:border-money focus:outline-none"
        />
        <span className="ml-auto text-xs text-text-tertiary">
          {filtered.length} of {items.length}
        </span>
      </div>

      {items.length === 0 ? (
        <div className="rounded-lg border border-border-subtle bg-surface-raised py-10 text-center text-sm text-text-tertiary">
          Nothing currently in limbo.
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-border-subtle">
            <table className="w-full min-w-[560px] table-auto text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle bg-surface-raised text-[11px] uppercase tracking-wide text-text-tertiary">
                  <SortHeader label="Order" sortKey="order_id" active={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortHeader label="State" sortKey="state" active={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortHeader label="Amount" sortKey="amount_paise" active={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <SortHeader label="Classified" sortKey="classified_at" active={sortKey} dir={sortDir} onClick={toggleSort} />
                </tr>
              </thead>
              <tbody>
                {pageItems.map((item) => {
                  const meta = stateMeta(item.state);
                  return (
                    <tr key={item.order_id} className="border-b border-border-subtle last:border-0 hover:bg-surface-raised/60">
                      <td className="px-3 py-2 font-mono text-xs text-text-secondary">{item.order_id}</td>
                      <td className="px-3 py-2">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${meta.badgeClass}`}>{meta.label}</span>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-text-primary">{formatRupees(item.amount_paise)}</td>
                      <td className="px-3 py-2 text-xs text-text-tertiary">{new Date(item.classified_at).toLocaleString()}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex items-center justify-between text-xs text-text-tertiary">
            <span>
              Page {clampedPage + 1} of {pageCount}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={clampedPage === 0}
                className="rounded border border-border px-2.5 py-1 disabled:opacity-30"
              >
                Prev
              </button>
              <button
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={clampedPage >= pageCount - 1}
                className="rounded border border-border px-2.5 py-1 disabled:opacity-30"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SortHeader({
  label,
  sortKey,
  active,
  dir,
  onClick,
  align,
}: {
  label: string;
  sortKey: SortKey;
  active: SortKey;
  dir: SortDir;
  onClick: (key: SortKey) => void;
  align?: "right";
}) {
  const isActive = active === sortKey;
  return (
    <th className={`px-3 py-2 font-medium ${align === "right" ? "text-right" : "text-left"}`}>
      <button onClick={() => onClick(sortKey)} className="inline-flex items-center gap-1 hover:text-text-secondary">
        {label}
        {isActive && <span>{dir === "asc" ? "↑" : "↓"}</span>}
      </button>
    </th>
  );
}
