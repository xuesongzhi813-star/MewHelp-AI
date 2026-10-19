.PHONY: kb-preview kb-build seed-conv kb-mine kb-vectorize kb-rebuild kb-reset

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
