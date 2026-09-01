import { EvalReport } from "@/lib/api";

export function EvalBatchPanel({ evalReport }: { evalReport: EvalReport | null }) {
  if (!evalReport?.available || !evalReport.report) {
    return <div className="py-8 text-center text-sm text-text-tertiary">No eval report yet. Run `make eval` to generate one.</div>;
  }

  const r = evalReport.report;
  return (
    <div>
      <p className="mb-4 text-xs text-text-tertiary">
        One self-contained synthetic batch, scored against its own ground truth — not the same population as the
        live totals above, which accumulate across every run this instance has ever processed. This is why the two
        panels show different &ldquo;revenue recovered&rdquo; numbers for what looks like the same metric.
      </p>
      <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
        <Metric label="Transactions" value={String(r.total_transactions)} />
        <Metric label="Detection accuracy" value={`${(r.detection.overall_accuracy * 100).toFixed(1)}%`} tone="money" />
        <Metric label="Diagnosis accuracy" value={`${(r.diagnosis.accuracy * 100).toFixed(1)}%`} tone="money" />
        <Metric label="False-intervention rate" value={`${(r.false_intervention.rate * 100).toFixed(2)}%`} />
        <Metric label="Revenue recovered" value={`Rs ${r.money.revenue_recovered_rupees.toFixed(2)}`} tone="money" />
        <Metric label="Loss prevented" value={`Rs ${r.money.loss_prevented_rupees.toFixed(2)}`} tone="money" />
        <Metric label="MTTR baseline" value={`${r.mttr.baseline_days} days`} />
        <Metric label="Agent resolution (this batch)" value={r.mttr.agent_resolution} />
      </dl>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "money" }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-text-tertiary">{label}</dt>
      <dd className={`mt-0.5 text-base font-medium ${tone === "money" ? "text-money" : "text-text-primary"}`}>{value}</dd>
    </div>
  );
}
