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

## Documentation

The main application lives in `harmonaize/`, and the primary setup and module documentation starts in `harmonaize/README.md`.

If you change setup flows, environment variables, or contributor workflow expectations, update the relevant markdown files in the same PR.