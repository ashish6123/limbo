"""Generates a synthetic batch of transaction scenarios as JSONL. Each line is
one scenario: an injected failure class (or none, for a healthy transaction),
a realistic amount, and a payment method. Ground truth for scoring — the
correct terminal reconciliation state and diagnosis root cause — is derived
from the injected class at scoring time in runner.py, not stored here twice.

Usage:
    python generator.py --count 500 --injection-rate 0.30 --seed 42 --out scenarios/batch_500.jsonl
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.chaos.enums import FailureClass  # noqa: E402

PAYMENT_METHODS = ["upi", "card", "netbanking"]
MIN_AMOUNT_RUPEES = 50
MAX_AMOUNT_RUPEES = 5000


def sample_amount_paise(rng: random.Random) -> int:
    """Log-normal-ish distribution: most orders are small, a long tail is large
    — mirrors typical e-commerce order value spread better than a uniform draw."""
    amount_rupees = int(rng.lognormvariate(5.3, 0.9))
    amount_rupees = max(MIN_AMOUNT_RUPEES, min(MAX_AMOUNT_RUPEES, amount_rupees))
    return amount_rupees * 100


def generate_scenarios(count: int, injection_rate: float, seed: int) -> list[dict]:
    rng = random.Random(seed)
    classes = list(FailureClass)
    injected_count = round(count * injection_rate)
    assignments: list[str | None] = [classes[i % len(classes)].value for i in range(injected_count)]
    assignments += [None] * (count - injected_count)
    rng.shuffle(assignments)

    return [
        {
            "id": f"scenario_{i:04d}",
            "amount_paise": sample_amount_paise(rng),
            "method": rng.choice(PAYMENT_METHODS),
            "injected_class": injected,
        }
        for i, injected in enumerate(assignments)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--injection-rate", type=float, default=0.30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default="scenarios/batch_500.jsonl")
    args = parser.parse_args()

    scenarios = generate_scenarios(args.count, args.injection_rate, args.seed)

    out_path = Path(__file__).resolve().parent / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for scenario in scenarios:
            f.write(json.dumps(scenario) + "\n")

    injected = sum(1 for s in scenarios if s["injected_class"] is not None)
    print(f"Generated {len(scenarios)} scenarios ({injected} injected, {len(scenarios) - injected} healthy) -> {out_path}")


if __name__ == "__main__":
    main()
