# Capability notes example

This checked example shows the first non-trivial Invariant graph:

```text
example:notes
    ↓ plugin dependency
example:notes-storage
    ↓ capability dependency
invariant:capability.fs_sandbox@0.2.0
```

The host grants sandbox writes through an execution policy. Neither plugin imports the filesystem capability implementation; dependencies arrive through `initialize(...)`.

Run from the repository root:

```bash
python examples/capability_notes/run.py
```

Expected output:

```text
Notes: governed dependency
```

See the [Dependencies and capabilities guide](../../docs/guides/dependencies-and-capabilities.md) for the full walkthrough and security boundary.
