# Security Policy

## Security boundary

Invariant provides policy-scoped capabilities and runtime isolation primitives, but those mechanisms are not a complete hostile-code sandbox.

- **In-process plugins are trusted code.** They execute in the host interpreter and must be treated with the same trust as the host application.
- **Capabilities are API and policy boundaries.** They restrict access made through the capability surface, but they cannot prevent trusted in-process Python code from using other Python APIs directly.
- **Isolated plugin workers are process boundaries, not full sandboxes.** They reduce coupling and contain some failures, but they do not by themselves provide sufficient isolation for hostile marketplace code.
- **Untrusted plugins require an external isolation layer** with explicit filesystem, network, process, resource, secret, and package-installation controls. Containers, OS sandboxes, dedicated users, or equivalent mechanisms belong above/beside Invariant.

Do not describe Invariant's capability model as a complete security sandbox.

## Sensitive surfaces

Security-sensitive areas include:

- filesystem and workspace containment
- HTTP allowlists and address validation
- plugin source loading
- isolated worker environment construction and IPC
- dependency/environment installation
- capability policy evaluation
- daemon and host interfaces
- path traversal, symlink, and atomic-write behavior
- secrets passed through configuration or environment variables

## Supported versions

Invariant is currently pre-1.0. Security fixes are applied to the current `main` line. Older snapshots are not guaranteed to receive backports.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting / Security Advisories for this repository when available.

If private reporting is unavailable, open a minimal public issue requesting a private security contact **without including exploit details, secrets, proof-of-concept payloads, or sensitive environment information**.

A useful private report includes:

- affected commit or version
- affected platform and Python version
- threat model and required attacker capabilities
- minimal reproduction steps
- expected vs. observed behavior
- impact assessment
- suggested mitigation, if known

## Dependency posture

Invariant has zero mandatory third-party runtime dependencies. Build and test tools are development-time dependencies and are outside the production runtime dependency boundary.
