import { ExceptionItem } from "@/lib/api";

export function ExceptionQueue({ items }: { items: ExceptionItem[] }) {
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-money/20 bg-money-dim py-10 text-center">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-money/20 text-money">
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
            <path
              fillRule="evenodd"
              d="M16.7 5.3a1 1 0 010 1.4l-7.5 7.5a1 1 0 01-1.4 0l-3.5-3.5a1 1 0 111.4-1.4l2.8 2.8 6.8-6.8a1 1 0 011.4 0z"
              clipRule="evenodd"
            />
          </svg>
        </div>
        <p className="text-sm font-medium text-money">All clear — no escalations</p>
        <p className="text-xs text-text-tertiary">Every case resolved within guardrails. Nothing needs a human.</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border-subtle">
      <table className="w-full min-w-[520px] table-auto text-left text-sm">
        <thead>
          <tr className="border-b border-border-subtle bg-surface-raised text-[11px] uppercase tracking-wide text-text-tertiary">
            <th className="px-3 py-2 font-medium">Order</th>
            <th className="px-3 py-2 font-medium">Action</th>
            <th className="px-3 py-2 font-medium">Reason</th>
            <th className="px-3 py-2 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={`${item.order_id}-${i}`} className="border-b border-border-subtle last:border-0">
              <td className="px-3 py-2 font-mono text-xs text-text-secondary">{item.order_id}</td>
              <td className="px-3 py-2">
                <span className="rounded-full bg-risk-dim px-2 py-0.5 text-xs font-medium text-risk border border-risk/30">
                  {item.action_type}
                </span>
              </td>
              <td className="px-3 py-2 text-text-secondary">{item.reasoning ?? "—"}</td>
              <td className="px-3 py-2 text-xs text-text-tertiary">{new Date(item.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
