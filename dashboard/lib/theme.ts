export interface StateMeta {
  label: string;
  hex: string;
  badgeClass: string;
}

export const STATE_META: Record<string, StateMeta> = {
  LIMBO_PAID_CANCELLED: {
    label: "Limbo / paid cancelled",
    hex: "#fbbf24",
    badgeClass: "bg-risk-dim text-risk border border-risk/30",
  },
  REFUND_STUCK: {
    label: "Refund stuck",
    hex: "#fb7185",
    badgeClass: "bg-danger-dim text-danger border border-danger/30",
  },
  DUPLICATE_PAYMENT: {
    label: "Duplicate payment",
    hex: "#c084fc",
    badgeClass: "bg-dup-dim text-dup border border-dup/30",
  },
  INVERSE_LEAK: {
    label: "Inverse leak",
    hex: "#f87171",
    badgeClass: "bg-danger-dim text-danger border border-danger/30",
  },
};

export function stateMeta(state: string): StateMeta {
  return (
    STATE_META[state] ?? {
      label: state,
      hex: "#5b6070",
      badgeClass: "bg-surface-raised text-text-secondary border border-border",
    }
  );
}
