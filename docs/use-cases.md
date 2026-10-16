# Use cases

Invariant is infrastructure for embedding independently developed Python components into an application. It is **not** an application framework: hosts decide which behavior to expose, which implementations satisfy application interfaces, and when operations are authorized.

The first and third use cases have runnable examples. The modular AI/media scenario is conceptual, not a bundled agent framework.

## 1. Extensible CLI, desktop app, or service

An application may support several independently maintained format handlers, exporters, or integrations.

```text
Application host (chooses application behavior)
              |
       Invariant Runtime
       /       |       \
    CSV       JSON     Report
  importer   exporter  backend
```

Each handler can be packaged as a versioned plugin with declared dependencies and managed initialization/shutdown. Invariant resolves and manages the components; the application defines the handler interface and chooses which implementation to invoke.

**Try it:** The [data importers example](https://github.com/kymuco/invariant-runtime/tree/main/examples/data_importers) processes CSV and JSON using separate plugins. Run `python examples/data_importers/run.py` and use `--plugin-dir` for an additional independently developed format implementation.

**Why use Invariant?** An independently developed plugin graph has lifecycle and versioning concerns beyond importing Python modules.

**When not to:** If handlers ship together and rarely change, ordinary imports or a small registration dictionary may be simpler. Invariant does not automatically infer interface compatibility or guarantee live hot-swapping.

## 2. Modular AI or media application

The same runtime model applies to components of a voice application or model-backed product:

```text
Application / coordinator
  - decides which backend to use
  - owns domain logic and user authorization
               |
        Invariant Runtime
         /      |      \
      Speech   Model   Storage
      backend  adapter backend
```

Here, a plugin might initialize a model-backed service, expose an application-defined Python interface, and free its resources during shutdown. Another plugin can declare a dependency on it.

**Why use Invariant?** Versions, dependencies, ownership, and startup/cleanup remain explicit even as components evolve independently.

**What Invariant does not do:** It does not provide LLM reasoning, agent planning, permission decisions, inference serving, or model-specific APIs. It is not an MCP implementation. An application may also use MCP or agent frameworks independently; they solve different integration problems. A declared component is not automatically interchangeable with another component unless the host supplies that contract.

This is a **conceptual scenario**, not an included AI-agent demonstration. No model download or API key is required to use Invariant.

## 3. Plugin dependencies with governed resources

This repository includes a runnable example, [capability_notes](https://github.com/kymuco/invariant-runtime/tree/main/examples/capability_notes), which composes two plugins and a capability:

```text
Host execution policy
         |
  notes@1.0.0
         |
  notes-storage@1.0.0
         |
  invariant:capability.fs_sandbox@0.3.0
```

The Notes plugin depends on Storage; Storage requests the filesystem capability by a concrete versioned reference. The host provides the execution policy and the runtime injects the permitted capability.

Run from the repository root:

```bash
python examples/capability_notes/run.py
```

Expected output:

```text
Notes: governed dependency
```

The [Dependencies and capabilities guide](guides/dependencies-and-capabilities.md) explains the manifests, host policy, dependency-first initialization, denial case, and cleanup.

**Why use Invariant?** The application does not manually wire the plugin dependency graph or expose concrete filesystem implementation objects. Capability declarations describe requirements, not permission grants.

**Security boundary:** In-process plugins are trusted Python code. Governed capability handles constrain operations through their interfaces; they cannot prevent trusted Python code from using unrelated APIs directly. Worker isolation is not a substitute for a hostile-code sandbox.

## Choosing whether to adopt

| Situation | Often enough | What Invariant adds |
| --- | --- | --- |
| A small static set of classes | Python imports or a DI container | Usually unnecessary overhead |
| Independent, versioned extensions | A plugin registry or loading framework | Declared dependencies, lifecycle, capabilities, strict version selection |
| Plugins need governed host services | Application-defined permission wrappers | Policy-scoped resource handles and dependency injection |
| Arbitrary untrusted extension marketplace | OS isolation / containers / a separate security layer | Does **not** solve hostile-code isolation |
| Replacing a running model without disruption | A domain-specific live-upgrade design | No general hot-swap or state-transfer guarantee |

See [Project status](project/status.md) and [Security](security.md) before designing a production deployment.
