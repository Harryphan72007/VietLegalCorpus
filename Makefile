.DEFAULT_GOAL := help
PY ?= python

.PHONY: help install test lint typecheck demo evaluate check

help:  ## show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## editable install + dev deps
	$(PY) -m pip install -e ".[dev]"

test:  ## run the test suite
	$(PY) -m pytest -q

lint:  ## ruff check + format check
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

typecheck:  ## mypy src
	$(PY) -m mypy src

demo:  ## produce a visible artifact in ./out
	@mkdir -p out
	$(PY) -m vietlegalcorpus.cli doctor | tee out/doctor.txt

evaluate:  ## placeholder until an evaluation harness lands (see plan)
	@echo "no evaluation harness yet"

check: lint typecheck test  ## CI gate + per-PR acceptance gate
