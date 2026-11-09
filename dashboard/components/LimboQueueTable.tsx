"use client";

import { useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ColDef,
  ICellRendererParams,
  ModuleRegistry,
  ValueFormatterParams,
  themeQuartz,
} from "ag-grid-community";
import { LimboQueueItem, formatMoney } from "@/lib/api";
import { STATE_META, stateMeta } from "@/lib/theme";

ModuleRegistry.registerModules([AllCommunityModule]);

// Matches the dashboard's dark surface tokens so the grid doesn't look bolted on.
const gridTheme = themeQuartz.withParams({
  backgroundColor: "#11131a",
  foregroundColor: "#e6e8ee",
  headerBackgroundColor: "#171a23",
  headerTextColor: "#9aa0ae",
  borderColor: "#262a36",
  rowHoverColor: "#1b1f2b",
  accentColor: "#34d399",
  fontSize: 13,
  headerFontSize: 12,
  spacing: 6,
  wrapperBorderRadius: 10,
});

function StateBadge({ value }: ICellRendererParams<LimboQueueItem, string>) {
  const meta = stateMeta(value ?? "");
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${meta.badgeClass}`}>{meta.label}</span>;
}

export function LimboQueueTable({ items }: { items: LimboQueueItem[] }) {
  const [quickFilter, setQuickFilter] = useState("");
  const [stateFilter, setStateFilter] = useState<string>("ALL");

  const rows = useMemo(
    () => (stateFilter === "ALL" ? items : items.filter((i) => i.state === stateFilter)),
    [items, stateFilter],
  );

  const columnDefs = useMemo<ColDef<LimboQueueItem>[]>(
    () => [
      { field: "order_id", headerName: "Order", flex: 1.3, minWidth: 150, filter: "agTextColumnFilter", cellClass: "font-mono" },
      { field: "payment_id", headerName: "Gateway payment / capture", flex: 1.3, minWidth: 170, filter: "agTextColumnFilter", cellClass: "font-mono" },
      { field: "state", headerName: "State", flex: 1.2, minWidth: 170, cellRenderer: StateBadge, filter: "agTextColumnFilter" },
      {
        field: "amount_paise",
        headerName: "Amount",
        flex: 0.8,
        minWidth: 110,
        type: "rightAligned",
        filter: "agNumberColumnFilter",
        valueFormatter: (p: ValueFormatterParams<LimboQueueItem, number>) =>
          p.value == null ? "" : formatMoney(p.value, p.data?.currency ?? "INR"),
      },
      {
        field: "classified_at",
        headerName: "Detected",
        flex: 1,
        minWidth: 150,
        sort: "desc",
        filter: "agDateColumnFilter",
        valueFormatter: (p: ValueFormatterParams<LimboQueueItem, string>) =>
          p.value ? new Date(p.value).toLocaleString() : "",
      },
    ],
    [],
  );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={quickFilter}
          onChange={(e) => setQuickFilter(e.target.value)}
          placeholder="Search order, capture id or state…"
          className="w-full max-w-xs rounded-md border border-border bg-surface-raised px-3 py-1.5 text-sm text-text-primary placeholder:text-text-tertiary focus:border-money focus:outline-none"
        />
        <select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          className="rounded-md border border-border bg-surface-raised px-2 py-1.5 text-sm text-text-primary"
          aria-label="Filter by state"
        >
          <option value="ALL">All states</option>
          {Object.entries(STATE_META).map(([key, meta]) => (
            <option key={key} value={key}>
              {meta.label}
            </option>
          ))}
        </select>
        <span className="ml-auto text-xs text-text-tertiary">
          {rows.length} of {items.length}
        </span>
      </div>
      <div style={{ height: 420 }}>
        <AgGridReact<LimboQueueItem>
          theme={gridTheme}
          rowData={rows}
          columnDefs={columnDefs}
          quickFilterText={quickFilter}
          pagination
          paginationPageSize={10}
          paginationPageSizeSelector={[10, 25, 50]}
          getRowId={(p) => p.data.order_id}
          defaultColDef={{ sortable: true, resizable: true, floatingFilter: false }}
          overlayNoRowsTemplate="<span style='color:#9aa0ae'>Nothing in limbo right now.</span>"
        />
      </div>
    </div>
  );
}
