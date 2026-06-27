.PHONY: run test eval lint fmt corpus-stats

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

corpus-stats:
	@echo "Corpus files:"
	@ls -la data/corpus/*.md 2>/dev/null || echo "  No corpus files found"
	@echo ""
	@echo "Eval cases:"
	@wc -l data/eval/cases.jsonl 2>/dev/null || echo "  No eval cases found"
