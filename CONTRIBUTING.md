# Contributing to Invariant Runtime

Thanks for helping make Invariant a better **small, dependency-free Python plugin runtime**. Contributions are welcome when they keep the public contracts narrow and the implementation product-agnostic.

## Where to start

- A bug: open a [bug report](https://github.com/kymuco/invariant-runtime/issues/new/choose) with the affected Python/OS version, a minimal reproduction, expected behavior and observed behavior.
- A new API or behavior: start with an issue explaining the *application-independent* problem and why the existing public facades do not solve it.
- Documentation or examples: submit a focused PR that a first-time developer can understand.
- Security-sensitive material: **do not** use a public issue or PR. Follow [SECURITY.md](SECURITY.md).

Avoid posting access tokens, machine-specific paths, private logs, user data, or material from other repositories.

## Keep the contract small

Invariant intentionally separates:

| Boundary | Responsibility |
| :-- | :-- |
| `invariant.api` | Supported host-facing facade |
| `invariant.spec` | Supported plugin-author contracts |
| `invariant.load`, `catalog`, `exec`, `ops` | Internal implementation that may change in the 0.x line |
| Calling application | Business behavior, intent, approval, retry decisions, trust policy |

Runtime code stays on the Python 3.11+ standard library; `pyproject.toml` has `dependencies = []`. Plugins are allowed to have their own Python dependencies; Invariant must not force them on every host.

Do not describe capability policies as a hostile-code sandbox, promise rollback of external side effects, or turn implementation-specific internal APIs into stable public contracts by accident.

## Development

With Python 3.11+:

```bash
python -m pip install -e ".[test]"
python examples/hello_plugin/run.py
python -m pytest -q tests/core
```

The default core test selection excludes the marked stress/soak suites. State explicitly when those heavier tests were not run.

Documentation uses an **optional**, pinned toolchain:

```bash
python -m pip install -e ".[docs]"
zensical build --clean --strict
```

No `[docs]` or `[test]` dependency belongs in the production runtime environment.

## Pull request quality

A reviewable PR should:

1. Explain the problem and the intended behavior, including what is deliberately out of scope.
2. Change only the necessary runtime, test, example or documentation files.
3. Add or update an executable regression test for behavior and contract changes.
4. Keep examples on `invariant.api` / `invariant.spec` rather than internal imports.
5. Report the **exact commands run** and their actual results. Never mark a check green without running it.
6. Document breaking changes to public 0.x contracts explicitly and preserve security boundaries.
7. Let maintainers review and merge; an issue discussion is not a compatibility promise.

Small fixes are preferable to speculative abstractions. A well-supported negative finding is also useful.

## Licensing of contributions

Invariant is licensed under [Apache License 2.0](LICENSE). Unless separately agreed or explicitly designated otherwise, contributions intentionally submitted for inclusion are governed by Apache-2.0 section 5. Only submit code and assets you have the rights to contribute; preserve required attribution and third-party notices.

See the [documentation home](docs/index.md), [public API](docs/reference/public-api.md) and [documentation style](docs/STYLE.md) before expanding user-facing interfaces.
