# Contributing

Invariant is maintained as a small, **stdlib-only Python runtime** with explicit public contracts. Contributions are welcome when they are narrow, testable and useful outside a particular application.

For the repository's full contribution workflow, see [CONTRIBUTING.md on GitHub](https://github.com/kymuco/invariant-runtime/blob/main/CONTRIBUTING.md). Security-sensitive reports belong on the [private reporting path](../security.md).

## A good first change

Documentation fixes, checked examples and narrowly scoped regressions are excellent first contributions. Before adding a new plugin system abstraction, explain the problem in an issue and confirm that `invariant.api` or `invariant.spec` cannot already express it.

## Verify locally

```bash
python -m pip install -e ".[test]"
python examples/hello_plugin/run.py
python -m pytest -q tests/core
```

For docs changes:

```bash
python -m pip install -e ".[docs]"
zensical build --clean --strict
```

Report executed commands and actual outcomes in the PR. Keep `invariant` itself free of mandatory third-party dependencies.

## What reviewers look for

Clear public behavior, safe lifecycle and rollback boundaries, correct versioned references, minimal integration cost, and tests that fail when a real regression occurs. Do not promise hostile-code sandboxing or universal external-effect rollback.
