.PHONY: run test eval lint fmt demo

run:
	uv run uvicorn keystone_engage.api:app --host 0.0.0.0 --port 8100 --reload

test:
	uv run pytest tests/ -v

eval:
	uv run python -m keystone_engage.eval

lint:
	uv run ruff check src/ tests/

fmt:
	uv run ruff format src/ tests/

demo:
	@echo "Keystone Engage demo mode - not yet implemented"
	@echo "Will serve governed conversational agent at http://localhost:8100"
