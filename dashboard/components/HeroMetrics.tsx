import { Summary, formatRupees } from "@/lib/api";

export function HeroMetrics({ summary }: { summary: Summary | null }) {
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-text-tertiary">
        Live totals — every transaction this instance has ever processed, including test/eval runs
      </h2>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-4">
        <HeroTile
          label="Money at risk"
          value={summary ? formatRupees(summary.money_at_risk_paise) : null}
          tone="risk"
        />
        <HeroTile
          label="Revenue recovered"
          value={summary ? formatRupees(summary.revenue_recovered_paise) : null}
          tone="money"
        />
        <HeroTile
          label="Loss prevented"
          value={summary ? formatRupees(summary.loss_prevented_paise) : null}
          tone="money"
        />
        <HeroTile
          label="MTTR baseline"
          value={summary ? `${summary.baseline_resolution_days} days` : null}
          tone="neutral"
          sub="measured agent MTTR: see eval batch panel"
        />
      </div>
    </section>
  );
}

function HeroTile({
  label,
  value,
  tone,
  sub,
}: {
  label: string;
  value: string | null;
  tone: "money" | "risk" | "neutral";
  sub?: string;
}) {
  const toneClass = tone === "money" ? "text-money" : tone === "risk" ? "text-risk" : "text-text-primary";
  return (
    <div className="bg-surface p-5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-text-tertiary">{label}</div>
      <div className={`mt-2 text-3xl font-semibold tabular-nums ${toneClass}`}>{value ?? "—"}</div>
      {sub && <div className="mt-1.5 text-[11px] text-text-tertiary">{sub}</div>}
    </div>
  );
}
