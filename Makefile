.PHONY: kb-preview kb-build seed-conv kb-mine kb-vectorize kb-rebuild kb-reset
.PHONY: kb-ch04-preview kb-ch04-migrate kb-ch04-verify

kb-ch04-preview:
	uv run python -m app.kb.ingest preview --corpus-version ch04

kb-ch04-migrate:
	uv run python -m app.kb.ingest migrate-hybrid

kb-ch04-verify:
	uv run python -m app.kb.ingest verify-hybrid

kb-preview:
	uv run python -m app.kb.ingest preview

kb-build:
	uv run python -m app.kb.ingest build

seed-conv:
	uv run python -m app.kb.seed_conversations

kb-mine:
	uv run python -m app.kb.mining --once

kb-vectorize:
	uv run python -m app.kb.ingest vectorize

kb-rebuild:
	uv run python -m app.kb.ingest rebuild

kb-reset:
	uv run python -m app.kb.ingest reset --yes
