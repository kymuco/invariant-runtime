# Hello plugin

This is the smallest checked end-to-end Invariant example.

Run it from the repository root after installing Invariant:

```bash
python -m pip install -e ".[test]"
python examples/hello_plugin/run.py
```

Expected output:

```text
Hello, Invariant!
```

Files:

- `plugin.py` — plugin implementation using only `invariant.spec`;
- `manifest.json` — Manifest v1 metadata;
- `run.py` — host using only `invariant.api`.

For the step-by-step explanation, read [Getting started](../../docs/getting-started.md).
