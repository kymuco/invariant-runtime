# Publication preflight — maintainer checklist

**Internal preparation note, not a declaration that the repository has been cleared for public release.**

Changing a GitHub repository from private to public exposes more than the present working tree. Review **the entire reachable commit history**, historical branches and tags, pull request discussions, attachments and Actions logs before any visibility change.

- [ ] Audit full reachable Git history, including deleted files, large blobs, old branches, tags and generated artifacts, for secrets, credentials, private URLs, machine paths and sensitive data.
- [ ] Revoke/rotate any credentials that were ever exposed, even if the newest commit no longer contains them.
- [ ] Review issue/PR comments and GitHub Actions logs for private paths, tokens, package hosts and internal project details.
- [ ] Verify authorship and license compatibility of all source, tests, examples, documentation, SVG assets and any inherited history; preserve necessary notices.
- [ ] Review old experimental notes and consumer-specific fixtures/probes; public README/site should not reference unrelated applications, and all remaining files should be consciously approved for publication.
- [ ] Review repository settings, branch protections, Dependabot, vulnerability-reporting access, forks and Actions token permissions.
- [ ] Validate a clean clone as an **external** developer, local quickstart, public imports, and all rendered documentation links.
- [ ] Confirm green core matrix, wheel smoke and strict site build on the exact pre-publication commit.
- [ ] Confirm GitHub Topics, short description, homepage, Issues and contribution channels after visibility is public.
- [ ] Only then change the visibility through GitHub using an explicit, separate maintainer decision.

Removing a filename from the documentation menu does **not** remove the file from Git history. Never declare the history safe based only on the latest `main` tree.
