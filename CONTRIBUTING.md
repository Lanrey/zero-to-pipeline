# Contributing

## Setup

```bash
git clone https://github.com/Lanrey/zero-to-pipeline.git
cd zero-to-pipeline
uv sync --extra dev
```

## Run locally

```bash
pipeline --help
pipeline source list
pipeline doctor
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest --cov=data_pipeline

# Lint
ruff check src/data_pipeline/

# Type check
mypy src/data_pipeline/
```

## Style

- Keep changes focused and minimal.
- Follow ruff's opinionated formatting (line length: 100).
- Prefer clear error messages over stack traces for user-facing errors.
- Update README with behavior changes.
- Add tests for new functionality.
