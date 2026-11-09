.PHONY: setup up down test verify-phase1 verify-phase2 verify-phase3 verify-phase4 verify-phase5 razorpay-checkout eval verify-all demo api dashboard

setup:
	cp -n .env.example .env || true
	pip install -r backend/requirements.txt

up:
	docker-compose up -d postgres

down:
	docker-compose down

test:
	cd backend && python -m pytest tests/ -q

verify-phase1:
	cd backend && python -m scripts.verify_phase1

verify-phase2:
	cd backend && python -m scripts.verify_phase2

verify-phase3:
	cd backend && python -m scripts.verify_phase3

verify-phase4:
	cd backend && python -m scripts.verify_phase4

verify-phase5:
	cd backend && python -m scripts.verify_phase5

# Phase 6 requires a human to complete one real checkout — see
# scripts/razorpay_manual_checkout.py for the printed next step.
razorpay-checkout:
	cd backend && python -m scripts.razorpay_manual_checkout

paypal-checkout:
	cd backend && python -m scripts.paypal_manual_checkout

eval:
	cd evals && python generator.py --count 500 --injection-rate 0.30 --seed 42 --out scenarios/batch_500.jsonl
	cd evals && python runner.py --scenarios scenarios/batch_500.jsonl --out results/report

demo: eval
	@echo "500-transaction chaos batch evaluated. See evals/results/report.md, or run 'make dashboard' to view it live."

api:
	cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

dashboard:
	cd dashboard && npm run dev

verify-all: test verify-phase1 verify-phase2 verify-phase3 verify-phase4 verify-phase5
