# AGENTS.md

Instructions for coding agents working in this repo.

## Releasing

- Always release with `./release.sh <version>`. Never bump `pyproject.toml`,
  `cronometer_mcp/__init__.py` or `server.json` by hand, and never hand-roll the bump commit, tag
  or push. The script checks for a clean tree and an unused tag, bumps all three files, commits,
  tags `v<version>` and pushes `main` with tags.
- Before running it: the working tree is clean and `python -m pytest` passes.
- Before running it: an adversarial Codex review of the unreleased diff has said ship. From Bash:
  `codex exec -c 'model_reasoning_effort="high"' -s read-only --skip-git-repo-check -o <out.md>
  "<prompt>" < /dev/null` (the `< /dev/null` stops it hanging on stdin). Ask it to attack named
  risks and reproduce, fix real findings with a failing test first, rerun until the verdict is
  SHIP. `codex mcp-server` no longer exists, so there is no MCP tool for this.
- Keep pushes to one per release: commit feature work locally without pushing and let
  `release.sh` make the only push (feature commits plus the bump commit and tag). Create the
  GitHub release with `gh release create v<version> --notes-file -`; that is an API call, not a
  push.
- The tag alone does not publish. Create a GitHub release from the tag; that triggers
  `.github/workflows/publish.yml`, which publishes to PyPI and then publishes `server.json` to the
  MCP Registry (GitHub OIDC, no secrets). Never publish to the registry by hand; if that job
  fails, re-run it with a manual `workflow_dispatch` of the same workflow, which skips PyPI.
