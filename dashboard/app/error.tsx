"use client";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-6 text-text-primary">
      <div className="max-w-md rounded-xl border border-danger/30 bg-danger-dim p-6 text-center">
        <p className="text-sm font-semibold text-danger">Something went wrong</p>
        <p className="mt-2 text-sm text-text-secondary">
          This page hit an unexpected error. Nothing was corrupted — try again.
        </p>
        <p className="mt-2 text-xs text-text-tertiary">{error.message}</p>
        <button
          onClick={reset}
          className="mt-4 rounded-lg bg-money px-4 py-2 text-sm font-semibold text-bg"
        >
          Try again
        </button>
      </div>
    </main>
  );
}
