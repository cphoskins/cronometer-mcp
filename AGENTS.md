# AGENTS.md

Instructions for coding agents working in this repo.

## Releasing

- Always release with `./release.sh <version>`. Never bump `pyproject.toml`,
  `cronometer_mcp/__init__.py` or `server.json` by hand, and never hand-roll the bump commit, tag
  or push. The script checks for a clean tree and an unused tag, bumps all three files, commits,
  tags `v<version>` and pushes `main` with tags.
- Before running it: the working tree is clean and `python -m pytest` passes.
- The tag alone does not publish. Create a GitHub release from the tag; that triggers
  `.github/workflows/publish.yml`, which builds the package and publishes it to PyPI.
