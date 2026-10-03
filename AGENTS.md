# AGENTS.md

Instructions for coding agents working in this repo.

## Releasing

- Always release with `./release.sh <version>`. Never bump `pyproject.toml`,
  `cronometer_mcp/__init__.py` or `server.json` by hand, and never hand-roll the bump commit, tag
  or push. The script checks for a clean tree and an unused tag, bumps all three files, commits,
  tags `v<version>` and pushes `main` with tags.
- Before running it: the working tree is clean and `python -m pytest` passes.
- The tag alone does not publish. Create a GitHub release from the tag; that triggers
  `.github/workflows/publish.yml`, which publishes to PyPI and then publishes `server.json` to the
  MCP Registry (GitHub OIDC, no secrets). Never publish to the registry by hand; if that job
  fails, re-run it with a manual `workflow_dispatch` of the same workflow, which skips PyPI.
