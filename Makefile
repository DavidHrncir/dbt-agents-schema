.PHONY: install lint typecheck test test-live validate-ci docs-check verify fixtures

# CI workflow and template files validated by validate-ci (SPEC 10.4). Missing files are
# skipped until the tasks that create them land.
GITHUB_WORKFLOWS := $(wildcard .github/workflows/*.yml examples/github/*.yml)
GITLAB_FILES := $(wildcard templates/gitlab/*.yml examples/gitlab/.gitlab-ci.yml)

install:
	uv sync --all-extras --group dev

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src

test:
	uv run pytest -m "not live"

test-live:
	uv run pytest -m live

validate-ci:
ifneq ($(strip $(GITHUB_WORKFLOWS)),)
	uv run check-jsonschema --builtin-schema vendor.github-workflows $(GITHUB_WORKFLOWS)
endif
ifneq ($(strip $(GITLAB_FILES)),)
	uv run python scripts/validate_gitlab_ci.py $(GITLAB_FILES)
endif
	@if command -v actionlint >/dev/null 2>&1; then \
		actionlint; \
	else \
		echo "actionlint not installed; skipping"; \
	fi

# The config reference in docs/partner-guide.md is generated from the JSON Schema.
docs-check:
	uv run python scripts/gen_config_reference.py --check

verify: lint typecheck test validate-ci docs-check

fixtures:
	bash scripts/build_fixtures.sh
