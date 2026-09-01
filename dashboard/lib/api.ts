const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DASHBOARD_API_KEY = process.env.NEXT_PUBLIC_DASHBOARD_API_KEY;

export interface Summary {
  money_at_risk_paise: number;
  revenue_recovered_paise: number;
  loss_prevented_paise: number;
  baseline_resolution_days: number;
  false_intervention_rate: number;
  circuit_breaker_threshold: number;
  executed_action_count: number;
}

export interface LimboQueueItem {
  order_id: string;
  payment_id: string | null;
  state: string;
  amount_paise: number;
  classified_at: string;
}

export interface ExceptionItem {
  order_id: string;
  payment_id: string | null;
  action_type: string;
  status: string;
  reasoning: string | null;
  created_at: string;
}

export interface AuditLogItem {
  stage: string;
  event: string;
  order_id: string | null;
  payment_id: string | null;
  reasoning: string | null;
  created_at: string;
}

export interface EvalReport {
  available: boolean;
  report?: {
    total_transactions: number;
    detection: { overall_accuracy: number };
    diagnosis: { accuracy: number };
    money: { revenue_recovered_rupees: number; loss_prevented_rupees: number };
    mttr: { baseline_days: number; agent_resolution: string };
    false_intervention: { count: number; executed_actions: number; rate: number };
    exceptions: unknown[];
  };
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    headers: DASHBOARD_API_KEY ? { "X-API-Key": DASHBOARD_API_KEY } : undefined,
  });
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status}`);
  }
  return res.json();
}

export const fetchSummary = () => getJson<Summary>("/api/summary");
export const fetchLimboQueue = () => getJson<LimboQueueItem[]>("/api/limbo-queue");
export const fetchExceptions = () => getJson<ExceptionItem[]>("/api/exceptions");
export const fetchAuditLog = () => getJson<AuditLogItem[]>("/api/audit-log?limit=50");
export const fetchEvalReport = () => getJson<EvalReport>("/api/eval-report");

export type DemoScenario = "replay" | "refund";

export interface DemoOrder {
  order_id: string;
  token: string;
  scenario: DemoScenario;
  amount_paise: number;
  checkout_url: string | null;
}

export interface DemoAuditEvent {
  id: number;
  stage: string;
  event: string;
  reasoning: string | null;
  created_at: string;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `${path} failed: ${res.status}`);
  }
  return res.json();
}

export const createDemoOrder = (scenario: DemoScenario) => postJson<DemoOrder>("/api/demo/orders", { scenario });

export const runFailureScenario = (scenario: DemoScenario) =>
  postJson<DemoOrder>("/api/demo/run-failure-scenario", { scenario });

export const dropDemoWebhook = (orderId: string, token: string) =>
  postJson<{ order_id: string; status: string }>(`/api/demo/webhook-drop/${orderId}`, { token });

export function demoStreamUrl(orderId: string, token: string): string {
  return `${API_BASE_URL}/api/demo/${orderId}/stream?token=${encodeURIComponent(token)}`;
}

export function formatRupees(paise: number): string {
  return (paise / 100).toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}
