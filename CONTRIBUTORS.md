# Contributors

This project is intended to be developed in the open, with changes that are easy to review, explain, and maintain.

## Working Style

We prefer pull requests that are:

1. Small enough to review in one sitting
2. Transparent about what changed and why
3. Focused on a single concern or tightly related set of changes
4. Consistent with standard practices used in large codebases

## Pull Request Expectations

When opening a pull request:

1. Keep the scope narrow. Avoid mixing refactors, feature work, and unrelated fixes in one PR.
2. Explain the intent clearly in the PR description. Reviewers should understand the problem, the approach, and any tradeoffs without reconstructing them from the diff.
3. Prefer incremental changes over large rewrites. If a larger effort is needed, split it into sequenced PRs where possible.
4. Update documentation when behavior, setup, or workflows change.
5. Validate the affected area before submitting. Run the narrowest useful checks for the code you touched.
6. Do not include unrelated formatting, generated files, or incidental cleanup unless the PR is explicitly for that purpose.

## Codebase Discipline

To keep the repository maintainable as it grows:

1. Preserve existing conventions unless there is a clear reason to change them.
2. Make behavior changes at the root cause instead of layering on workarounds.
3. Keep interfaces stable when possible, and call out any breaking change explicitly.
4. Leave a clear paper trail in commit messages, PR descriptions, and updated docs.

## Security Vulnerabilities in Commits and Pull Requests

This repository is public and its history is permanent, so an unfixed vulnerability written into it tells attackers exactly where to look.

1. Never describe an outstanding (unfixed) security vulnerability in a commit message, branch name, pull request title or description, review comment, issue, or discussion. That includes vulnerable dependencies that haven't been upgraded yet, and scanner output (Trivy, Dependabot, CodeQL) that lists unfixed findings.
2. Track outstanding vulnerabilities only in private places: the repository's [Security tab](https://github.com/drnicholasbrink/HarmonAIze/security) (Dependabot alerts, code scanning, private draft advisories), or privately with the maintainers.
3. When you fix a vulnerability, describe the change neutrally (for example "Restrict study views to project members" or "Update Django to 5.2.18"). Don't list remaining or related weaknesses that the change leaves open.
4. Name a fixed vulnerability publicly (CVE, advisory ID, impact) only once the fix has been deployed and the maintainers have published the advisory.
5. If you spot an unfixed vulnerability already written somewhere public, report it privately as described in [SECURITY.md](SECURITY.md) so it can be removed.

## Dependency Security

Dependencies are monitored automatically:

- **Dependabot** raises alerts for vulnerable dependencies and opens weekly version-update pull requests (config: [`.github/dependabot.yml`](.github/dependabot.yml)).
- **Trivy** ([`.github/workflows/security.yml`](.github/workflows/security.yml)) scans the production image and the Docker/Terraform configuration on every pull request to `main` and weekly. The build fails if a CRITICAL vulnerability with an available fix is present.

Review results in the Security tab. Don't copy them into public pull requests or issues.

## Documentation

The main application lives in `harmonaize/`, and the primary setup and module documentation starts in the repository [README.md](README.md). The issue labels, priorities and pick-up workflow are described in its "Contributing and issue workflow" section. Report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

If you change setup flows, environment variables, or contributor workflow expectations, update the relevant markdown files in the same PR.