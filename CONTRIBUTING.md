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

PEP 8 is the project's style guide. Key rules enforced by ruff:

- **Indentation**: 4 spaces, no tabs.
- **Line length**: 100 characters (project override of PEP 8's 79).
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_CASE` for constants.
- **Blank lines**: Two blank lines between top-level functions/classes; one blank line between methods.
- **Imports**: Grouped and sorted (stdlib third party first party).
- **Comments**: Docstrings on all public modules, classes, and functions. Block comments explain *why* not *what*.
- **Boolean checks**: Use `if x:` not `if x == True:`; use `if not x:` not `if len(x) == 0:`; use `if x is not None:` not `if x:` when checking for `None`.
- **String checks**: Use `.startswith()` / `.endswith()` instead of slicing.
- **Comparisons**: Use `is not` over `not ... is`.

### Pre-submit checklist

Before committing, run:

```bash
pytest tests/ -v          # all tests pass
ruff check src/data_pipeline/  # zero lint errors
```

Keep changes focused and minimal. Prefer clear error messages over stack traces for user-facing errors. Update docs with behavior changes. Add tests for new functionality.
