.PHONY: install run doctor

install:
	cd python && uv sync

run:
	cd python && uv run pipeline $(ARGS)

doctor:
	cd python && uv run pipeline doctor
