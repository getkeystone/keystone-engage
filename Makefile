.PHONY: run test eval eval-run eval-seal eval-audit-dump lint fmt corpus-stats

# Default eval target URL. Override: make eval-run BASE_URL=http://<host>:8100
BASE_URL ?= http://localhost:8100

run:
	uv run uvicorn keystone_engage.api:app --host 0.0.0.0 --port 8100 --reload

test:
	uv run pytest tests/ -v

# Legacy target (localhost). Prefer eval-run with explicit BASE_URL.
eval:
	uv run python -m keystone_engage.eval

# Run eval against a live endpoint. Produces timestamped results in data/eval/results/.
# Usage:
#   make eval-run
#   make eval-run BASE_URL=http://localhost:8100
eval-run:
	uv run python -m keystone_engage.eval $(BASE_URL)

# Seal a completed run into publishable artifacts under evals/agent-vN/.
# Usage: make eval-seal VERSION=agent-v1 RUN_ID=eval-20260708T024200
eval-seal:
	@test -n "$(VERSION)" || (echo "ERROR: VERSION required (e.g., agent-v1)" && exit 1)
	@test -n "$(RUN_ID)" || (echo "ERROR: RUN_ID required (e.g., eval-20260708T024200)" && exit 1)
	python3 evals/seal_results.py $(VERSION) $(RUN_ID)
	@echo ""
	@echo "Sealed to evals/$(VERSION)/results.json"
	@echo "Next: update evals/$(VERSION)/run_metadata.json and evals/$(VERSION)/report.md"

# Dump audit chain entries from the database for a specific time window.
# Usage: make eval-audit-dump VERSION=agent-v1 AFTER="2026-07-08T02:00:00" BEFORE="2026-07-08T03:00:00"
#   Set DB_HOST (defaults to localhost) and DB_URL/KEYSTONE_DATABASE_URL from .env.
DB_HOST ?= localhost
eval-audit-dump:
	@test -n "$(VERSION)" || (echo "ERROR: VERSION required" && exit 1)
	@test -n "$(AFTER)" || (echo "ERROR: AFTER required (ISO timestamp)" && exit 1)
	@test -n "$(BEFORE)" || (echo "ERROR: BEFORE required (ISO timestamp)" && exit 1)
	@mkdir -p evals/$(VERSION)
	psql -h $(DB_HOST) -U keystone -d keystone_engage -t -A -c \
		"SELECT json_agg(row_to_json(t)) FROM ( \
			SELECT id, timestamp, event_type, actor, prev_hash, curr_hash, \
				agent_id, tempo, task_id, input_tokens, output_tokens, \
				model_used, cost_cents, latency_ms \
			FROM audit_entries \
			WHERE timestamp >= '$(AFTER)' AND timestamp < '$(BEFORE)' \
			ORDER BY id ASC \
		) t" > evals/$(VERSION)/audit_chain_dump.json
	@echo "Dumped audit chain to evals/$(VERSION)/audit_chain_dump.json"
	@python3 -c "import json; d=json.load(open('evals/$(VERSION)/audit_chain_dump.json')); print(f'  {len(d)} entries')" 2>/dev/null || echo "  (verify manually)"

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
	@echo ""
	@echo "Sealed evals:"
	@ls -d evals/agent-*/ 2>/dev/null || echo "  No sealed evals"
