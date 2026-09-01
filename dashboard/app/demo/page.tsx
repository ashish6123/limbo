"use client";

import { useEffect, useRef, useState } from "react";
import {
  DemoAuditEvent,
  DemoOrder,
  DemoScenario,
  createDemoOrder,
  demoStreamUrl,
  dropDemoWebhook,
  formatRupees,
  runFailureScenario,
} from "@/lib/api";

type Step = "pick" | "awaiting_checkout" | "streaming" | "done";

const SCENARIO_LABEL: Record<DemoScenario, string> = {
  replay: "Fulfillable window still open — webhook replay should recover the sale",
  refund: "Fulfillable window has passed — the payment should be auto-refunded",
};

const OUTCOME_COPY: Record<DemoScenario, { title: string; state: string }> = {
  replay: { title: "Recovery Complete", state: "Order confirmed via REPLAY_WEBHOOK" },
  refund: { title: "Refund Issued", state: "Payment auto-refunded via AUTO_REFUND" },
};

export default function DemoPage() {
  const [scenario, setScenario] = useState<DemoScenario>("replay");
  const [step, setStep] = useState<Step>("pick");
  const [order, setOrder] = useState<DemoOrder | null>(null);
  const [events, setEvents] = useState<DemoAuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => eventSourceRef.current?.close();
  }, []);

  function startStream(target: DemoOrder) {
    setEvents([]);
    setStep("streaming");
    const source = new EventSource(demoStreamUrl(target.order_id, target.token));
    eventSourceRef.current = source;
    source.onmessage = (msg) => {
      const payload: DemoAuditEvent = JSON.parse(msg.data);
      setEvents((prev) => [...prev, payload]);
      if (payload.stage === "outcome") {
        setStep("done");
        source.close();
      }
    };
    source.onerror = () => {
      setError("lost connection to the event stream");
      source.close();
    };
  }

  async function handleMakeTestPayment() {
    setError(null);
    setBusy(true);
    try {
      const created = await createDemoOrder(scenario);
      setOrder(created);
      if (created.checkout_url) {
        window.open(created.checkout_url, "_blank", "noopener,noreferrer");
        setStep("awaiting_checkout");
      } else {
        await dropDemoWebhook(created.order_id, created.token);
        startStream(created);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to create demo order");
    } finally {
      setBusy(false);
    }
  }

  async function handleIvePaid() {
    if (!order) return;
    setError(null);
    setBusy(true);
    try {
      await dropDemoWebhook(order.order_id, order.token);
      startStream(order);
    } catch (e) {
      setError(e instanceof Error ? e.message : "the payment may not have captured yet — try again in a few seconds");
    } finally {
      setBusy(false);
    }
  }

  async function handleRunFailureScenario() {
    setError(null);
    setBusy(true);
    try {
      const created = await runFailureScenario(scenario);
      setOrder(created);
      startStream(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to run the scenario");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    eventSourceRef.current?.close();
    setOrder(null);
    setEvents([]);
    setError(null);
    setStep("pick");
  }

  return (
    <main className="min-h-screen bg-bg px-6 py-8 text-text-primary md:px-10">
      <div className="mx-auto max-w-2xl">
        <header className="mb-8">
          <h1 className="text-xl font-semibold tracking-tight">Limbo — Live Demo</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Watch one real payment go into limbo and recover, stage by stage, in real time.
          </p>
        </header>

        {error && (
          <div className="mb-6 rounded-lg border border-danger/30 bg-danger-dim p-3 text-sm text-danger">{error}</div>
        )}

        {step === "pick" && (
          <div className="rounded-xl border border-border-subtle bg-surface p-6">
            <p className="mb-3 text-sm font-medium text-text-primary">Choose what to demonstrate</p>
            <div className="mb-6 space-y-2">
              {(Object.keys(SCENARIO_LABEL) as DemoScenario[]).map((key) => (
                <label
                  key={key}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm ${
                    scenario === key ? "border-money/50 bg-money-dim" : "border-border-subtle"
                  }`}
                >
                  <input
                    type="radio"
                    name="scenario"
                    className="mt-1"
                    checked={scenario === key}
                    onChange={() => setScenario(key)}
                  />
                  <span className="text-text-secondary">{SCENARIO_LABEL[key]}</span>
                </label>
              ))}
            </div>
            <p className="mb-4 text-xs text-text-tertiary">
              Fixed demo amount: {formatRupees(100_000)} · Premium Plan
            </p>
            <div className="flex flex-col gap-3 sm:flex-row">
              <button
                onClick={handleMakeTestPayment}
                disabled={busy}
                className="flex-1 rounded-lg bg-money px-4 py-2.5 text-sm font-semibold text-bg disabled:opacity-50"
              >
                Make Test Payment
              </button>
              <button
                onClick={handleRunFailureScenario}
                disabled={busy}
                className="flex-1 rounded-lg border border-border-subtle bg-surface-raised px-4 py-2.5 text-sm font-semibold text-text-primary disabled:opacity-50"
              >
                Run Failure Scenario
              </button>
            </div>
          </div>
        )}

        {step === "awaiting_checkout" && order && (
          <div className="rounded-xl border border-border-subtle bg-surface p-6">
            <p className="mb-2 text-sm font-medium text-text-primary">Complete the real Razorpay test-mode checkout</p>
            <p className="mb-4 text-sm text-text-secondary">
              A checkout tab has opened. Use test card 4111 1111 1111 1111, any future expiry, any CVV. Once you&apos;ve
              paid, come back and click continue.
            </p>
            <a
              href={order.checkout_url ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="mb-4 block break-all text-sm text-money underline"
            >
              {order.checkout_url}
            </a>
            <button
              onClick={handleIvePaid}
              disabled={busy}
              className="w-full rounded-lg bg-money px-4 py-2.5 text-sm font-semibold text-bg disabled:opacity-50"
            >
              I&apos;ve completed the payment
            </button>
          </div>
        )}

        {(step === "streaming" || step === "done") && order && (
          <div className="rounded-xl border border-border-subtle bg-surface p-6">
            <div className="mb-4 flex items-center gap-2 text-[11px] text-text-tertiary">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${step === "streaming" ? "live-dot bg-money" : "bg-text-tertiary"}`} />
              {step === "streaming" ? "live — pipeline in progress" : "pipeline finished"}
            </div>
            <ol className="space-y-3">
              {events.map((e) => (
                <li key={e.id} className="rounded-lg border border-border-subtle p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-text-primary">{e.stage}: {e.event}</span>
                    <span className="text-xs text-text-tertiary">{new Date(e.created_at).toLocaleTimeString()}</span>
                  </div>
                  {e.reasoning && <p className="mt-1 text-text-tertiary">{e.reasoning}</p>}
                </li>
              ))}
              {step === "streaming" && events.length === 0 && (
                <li className="text-sm text-text-tertiary">Waiting for the reconciliation pipeline to pick this up…</li>
              )}
            </ol>

            {step === "done" && (
              <div className="mt-6 rounded-lg border border-money/30 bg-money-dim p-4">
                <p className="text-sm font-semibold text-money">{OUTCOME_COPY[order.scenario].title}</p>
                <p className="mt-1 text-sm text-text-secondary">{OUTCOME_COPY[order.scenario].state}</p>
                <p className="mt-1 text-sm text-text-secondary">Amount: {formatRupees(order.amount_paise)}</p>
                <p className="mt-1 text-sm text-text-secondary">Order: <span className="font-mono text-xs">{order.order_id}</span></p>
                <div className="mt-3 flex items-center gap-4">
                  <a href="/" className="text-sm text-money underline">
                    View full audit trail
                  </a>
                  <button onClick={reset} className="text-sm text-text-secondary underline">
                    Run another demo
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
