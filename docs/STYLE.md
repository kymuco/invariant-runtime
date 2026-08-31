# Documentation standard

Invariant documentation is part of the product surface. It should be reviewed with the same care as public API.

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

The first plugin does not need Python isolation, capability internals, version epochs, or every dependency scope explained inline. Link to the deeper page instead.

### 4. Keep concepts separate

Use one term for one idea.

In particular:

- **plugin dependency** means another Invariant plugin or capability;
- **Python dependency** means a package installed into a plugin environment;
- **capability** means a runtime-owned policy/API handle;
- **isolation** does not automatically mean hostile-code sandboxing.

### 5. Prefer public imports in user-facing examples

Tutorials and ordinary guides should use:

```text
invariant.api
invariant.spec
```

Do not teach implementation-level imports unless the page is explicitly for advanced integration or runtime development.

### 6. State security boundaries directly

Do not use wording that could imply stronger isolation than the runtime provides.

Whenever a feature touches filesystem, network, processes, secrets, or untrusted code, make the trust assumption explicit and link to `SECURITY.md` when relevant.

### 7. Show expected results

Commands should include the important expected output when that output helps the reader know they succeeded.

### 8. Make examples executable

Repository examples are test fixtures, not decorative snippets.

When a public workflow changes, update the example and its regression test in the same change.

### 9. Keep reference normative and guides explanatory

Reference pages answer questions such as:

- What fields exist?
- What type is accepted?
- What is the default?
- What is supported?

Guides answer:

- What should I do?
- Why should I choose this approach?
- What mistakes should I avoid?

Do not mix the two until both become harder to use.

### 10. Earn every sentence

Prefer direct, precise language. Remove repeated claims, marketing filler, and implementation trivia that does not help the reader make a decision or complete a task.

## Page structure

A task-oriented page should usually contain:

1. a one-sentence purpose;
2. prerequisites, if any;
3. the shortest working path;
4. expected result;
5. a concise explanation of what happened;
6. links to the next layer of detail.

A reference page should usually contain:

1. scope and version;
2. minimal form;
3. field/API tables;
4. constraints and defaults;
5. complete example;
6. related reference links.

## Review checklist

Before merging documentation, ask:

- Can a first-time reader tell what this page is for in the first paragraph?
- Is there an unnecessary concept before the first useful action?
- Do examples use supported public imports?
- Are defaults, constraints, and security assumptions explicit?
- Are relative links valid?
- Does the example still execute against the current runtime?
- Does the page distinguish current capability from future intent?
- Can any sentence be removed without losing useful information?
