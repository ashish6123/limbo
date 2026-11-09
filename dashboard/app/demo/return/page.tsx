export default function PayPalReturnPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-4 text-text-primary">
      <div className="max-w-md rounded-xl border border-border-subtle bg-surface p-6 text-center">
        <p className="text-sm font-semibold text-money">PayPal approval received</p>
        <p className="mt-2 text-sm text-text-secondary">
          You can close this tab. Go back to the Limbo demo tab and click{" "}
          <b>I&apos;ve completed the payment</b>: Limbo will capture the sandbox payment and start the recovery run.
        </p>
      </div>
    </main>
  );
}
