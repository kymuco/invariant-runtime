# Documentation standard

Invariant documentation is part of the product surface. Review it with the same care as public API.

## Product test: the first 30 seconds

A first-time reader landing on the repository should be able to answer four questions quickly:

1. What is Invariant?
2. What problem does it solve?
3. Where do I start?
4. What security boundary should I not misunderstand?

If the front page makes the reader study internal architecture before answering those questions, the documentation is too expensive to enter.

## Principles

### 1. Lead with purpose

Open every page by telling the reader what the page helps them accomplish or understand.

Avoid starting with repository history, implementation detail, or terminology that has not been defined yet.

### 2. Give the shortest working path first

A new user should reach a correct result before being asked to learn optional complexity.

Prefer:

```text
install → write plugin → write manifest → run host → expected output
```

over a tour of every subsystem.

### 3. Use progressive disclosure

Introduce advanced features only when they become relevant.

The first plugin does not need Python isolation, capability internals, version epochs, every dependency scope, or registry construction explained inline. Link to the deeper page instead.

### 4. Write for scanning

Readers rarely consume technical documentation linearly on the first pass.

Use:

- descriptive headings;
- short paragraphs;
- tables for stable field/API facts;
- code blocks for exact syntax;
- lists only when the items are genuinely parallel.

A reader should be able to recover the structure of a page from its headings alone.

### 5. Keep concepts separate

Use one term for one idea.

In particular:

- **plugin dependency** means another Invariant plugin or capability;
- **Python dependency** means a package installed into a plugin environment;
- **capability** means a runtime-owned policy/API handle;
- **isolation** does not automatically mean hostile-code sandboxing.

Do not introduce an internal implementation term as a core user concept unless the user must make a decision about it.

### 6. Prefer public imports in user-facing examples

Tutorials and ordinary guides should use:

```text
invariant.api
invariant.spec
```

Do not teach implementation-level imports unless the page is explicitly for advanced integration or runtime development.

### 7. State security boundaries directly

Do not use wording that could imply stronger isolation than the runtime provides.

Whenever a feature touches filesystem, network, processes, secrets, package installation, or untrusted code, state the relevant trust assumption and link to `SECURITY.md` when useful.

### 8. Show expected results

Commands should include the important expected output when that output helps the reader know they succeeded.

For a first-run tutorial, success must be visually obvious.

### 9. Make examples executable and canonical

Repository examples are test fixtures, not decorative snippets.

When a public workflow changes, update the example and its regression test in the same change.

Tutorial snippets that claim to reproduce a checked example should stay synchronized with the checked files rather than becoming a second hand-maintained implementation.

### 10. Keep reference normative and guides explanatory

Reference pages answer:

- What exists?
- What type is accepted?
- What is required?
- What is the default?
- What is supported?

Guides answer:

- What should I do?
- Why should I choose this approach?
- What mistakes should I avoid?

Do not make readers extract normative facts from narrative prose when a compact table is clearer.

### 11. Keep the README a front door

The repository README should explain the product, prove the first useful path, route readers to the correct depth, state the security boundary, and communicate project status.

It should not duplicate the full API reference, manifest reference, or contributor architecture guide.

### 12. Earn every sentence

Prefer direct, precise language. Remove repeated claims, marketing filler, unnecessary adjectives, and implementation trivia that does not help the reader make a decision or complete a task.

Simplicity is not the absence of detail. It is placing detail at the depth where it becomes useful.

## Rendered site

The rendered documentation site is a presentation layer over the Markdown sources. Content remains canonical in the repository; site configuration and styling must not create a second source of truth.

Install the documentation toolchain with:

```bash
python -m pip install -e ".[docs]"
```

Preview locally:

```bash
zensical serve
```

Before merging site or documentation changes, run a clean strict build:

```bash
zensical build --clean --strict
```

The documentation generator is pinned in the `docs` optional dependency so local and CI builds are reproducible. It is a development dependency and does not change Invariant's zero-runtime-dependency contract.

## Page structure

A task-oriented page should usually contain:

1. a one-sentence purpose;
2. prerequisites, if any;
3. the shortest working path;
4. expected result;
5. a concise explanation of what happened;
6. links to the next layer of detail.

A concept page should usually contain:

1. the smallest mental model that explains the subject;
2. public concepts before internal machinery;
3. one concrete example for each important concept;
4. deeper implementation detail only after the model is established.

A reference page should usually contain:

1. scope and version;
2. minimal form;
3. compact field/API tables;
4. constraints and defaults;
5. complete example when useful;
6. related reference links.

## Review checklist

Before merging documentation, ask:

- Can a first-time reader tell what this page is for in the first paragraph?
- Can the page be understood by scanning headings before reading every paragraph?
- Is there an unnecessary concept before the first useful action?
- Do examples use supported public imports?
- Are defaults, constraints, and security assumptions explicit?
- Are relative links valid?
- Does the checked example still execute against the current runtime?
- Are tutorial snippets synchronized with their canonical example files?
- Does the page distinguish current capability from future intent?
- Is the README routing readers instead of duplicating deeper reference material?
- Does `zensical build --clean --strict` succeed from a clean docs environment?
- Can any sentence be removed without losing useful information?
