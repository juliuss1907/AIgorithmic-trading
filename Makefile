.PHONY: test web research-worker paper-worker install-paper-timer paper-timer-status paper-alert-test uninstall-paper-timer

PORT ?= 8000

test:
	uv run --frozen pytest -q

web:
	uv run --frozen uvicorn lab.web:app --host 127.0.0.1 --port $(PORT)

research-worker:
	uv run --frozen python -m lab.jobs

paper-worker:
	uv run --frozen python -m lab.paper_worker

install-paper-timer:
	./scripts/paper-timer.sh install

paper-timer-status:
	./scripts/paper-timer.sh status

paper-alert-test:
	./scripts/paper-timer.sh alert-test

uninstall-paper-timer:
	./scripts/paper-timer.sh uninstall
