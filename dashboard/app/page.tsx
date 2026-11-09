"use client";

import { useEffect, useState } from "react";
import {
  AuditLogItem,
  EvalReport,
  ExceptionItem,
  LimboQueueItem,
  Summary,
  fetchAuditLog,
  fetchEvalReport,
  fetchExceptions,
  fetchLimboQueue,
  fetchSummary,
} from "@/lib/api";
import { HeroMetrics } from "@/components/HeroMetrics";
import { QueueByStateChart } from "@/components/QueueByStateChart";
import { MttrChart } from "@/components/MttrChart";
import { LimboQueueTable } from "@/components/LimboQueueTable";
import { ExceptionQueue } from "@/components/ExceptionQueue";
import { AuditTrail } from "@/components/AuditTrail";
import { EvalBatchPanel } from "@/components/EvalBatchPanel";

const REFRESH_INTERVAL_MS = 5000;

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [limboQueue, setLimboQueue] = useState<LimboQueueItem[]>([]);
  const [exceptions, setExceptions] = useState<ExceptionItem[]>([]);
  const [auditLog, setAuditLog] = useState<AuditLogItem[]>([]);
  const [evalReport, setEvalReport] = useState<EvalReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [s, lq, exc, log, ev] = await Promise.all([
          fetchSummary(),
          fetchLimboQueue(),
          fetchExceptions(),
          fetchAuditLog(),
          fetchEvalReport(),
        ]);
        if (cancelled) return;
        setSummary(s);
        setLimboQueue(lq);
        setExceptions(exc);
        setAuditLog(log);
        setEvalReport(ev);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "failed to load");
      }
    }

    load();
    const interval = setInterval(load, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <main className="min-h-screen bg-bg px-6 py-8 text-text-primary md:px-10">
      <header className="mx-auto mb-8 max-w-7xl">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-money-dim text-money">
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
              <path d="M10 2a1 1 0 011 1v1.06A6.98 6.98 0 0116.94 9H18a1 1 0 110 2h-1.06A6.98 6.98 0 0111 16.94V18a1 1 0 11-2 0v-1.06A6.98 6.98 0 013.06 11H2a1 1 0 110-2h1.06A6.98 6.98 0 019 3.06V2a1 1 0 011-1zm0 4a5 5 0 100 10 5 5 0 000-10z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Limbo</h1>
            <p className="text-sm text-text-secondary">
              AI reconciliation agent that finds captured PayPal payments the merchant never saw, and recovers them before they become disputes
            </p>
          </div>
          <div className="ml-auto hidden items-center gap-2 sm:flex">
            {summary && (
              <>
                <span className="rounded-full border border-border bg-surface-raised px-2.5 py-1 text-xs text-text-secondary">
                  Gateway: {summary.gateway_mode === "paypal" ? "PayPal sandbox" : summary.gateway_mode}
                </span>
                <span className="rounded-full border border-border bg-surface-raised px-2.5 py-1 text-xs text-text-secondary">
                  AI: {summary.ai_mode === "claude" ? "Claude investigator" : "deterministic fallback"}
                </span>
              </>
            )}
            <a href="/demo" className="rounded-lg bg-money px-3 py-1.5 text-sm font-semibold text-bg">
              Live demo
            </a>
          </div>
        </div>
      </header>

      {error && (
        <div className="mx-auto mb-6 max-w-7xl rounded-lg border border-danger/30 bg-danger-dim p-3 text-sm text-danger">
          Could not reach the API ({error}). Is the backend running on{" "}
          {process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}?
        </div>
      )}

      <div className="mx-auto max-w-7xl">
        <HeroMetrics summary={summary} />

        <section className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Panel title="Limbo queue by state">
            <QueueByStateChart items={limboQueue} />
          </Panel>
          <Panel title="Resolution time: Limbo vs. manual baseline">
            <MttrChart summary={summary} />
          </Panel>
        </section>

        <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Panel title={`Limbo queue (${limboQueue.length})`} className="lg:col-span-2">
            <LimboQueueTable items={limboQueue} />
          </Panel>

          <Panel title={`Exception queue: escalated to a human (${exceptions.length})`} className="lg:col-span-2">
            <ExceptionQueue items={exceptions} />
          </Panel>

          <Panel title="Audit trail" className="lg:col-span-2">
            <AuditTrail items={auditLog} />
          </Panel>

          <Panel title="Latest eval batch (isolated 500-txn benchmark)" className="lg:col-span-2">
            <EvalBatchPanel evalReport={evalReport} />
          </Panel>
        </section>
      </div>
    </main>
  );
}

function Panel({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-border-subtle bg-surface p-5 shadow-[0_1px_0_0_rgba(255,255,255,0.02)_inset] ${className ?? ""}`}>
      <h2 className="mb-4 text-sm font-semibold text-text-primary">{title}</h2>
      {children}
    </div>
  );
}
