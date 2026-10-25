# Spec — Streamable HTTP transport

**Origin:** feature request from `phazethru`,
[discussion #12](https://github.com/cphoskins/cronometer-mcp/discussions/12), 2026-10-24. They run
the server in a remote Docker container behind `mcpo`, and `mcpo` no longer works with the `mcp`
package that 2.3.0 requires. A native HTTP endpoint removes `mcpo` from their stack.

**Status:** shipped in 2.4.0 (2026-10-24). 201 tests pass; live `list_tools` over HTTP returns
27 tools, 14 read-only; hostile `Host` rejected with 421 when the allowlist is set. Codex review:
round 1 found the DNS-rebinding gap (fixed with `CRONOMETER_ALLOWED_HOSTS`), round 2 SHIP.

---

## Established facts (verified against mcp 2.3.0, do not re-derive)

- `MCPServer.run(transport="streamable-http", host=..., port=...)` is supported natively. Defaults
  are `host="127.0.0.1"`, `port=8000`, path `/mcp`.
- `uvicorn` and `starlette` are hard dependencies of `mcp` already. No new packages.
- DNS-rebinding protection (Host/Origin header checks) is auto-enabled only when the bind host is
  `127.0.0.1`, `localhost` or `::1`. Any other host (for example `0.0.0.0` in a container) gets no
  header checks, so a Docker publish works without further configuration.
- The server has no authentication of its own. Whoever can reach the port can act as the
  Cronometer account whose credentials are in the environment.

## Design decisions

- **Variable names follow the existing `CRONOMETER_*` convention**, not the requester's
  `STREAMABLE_HTTP=true`. A transport name (`CRONOMETER_TRANSPORT`) covers a future `sse` option
  without adding another boolean, and reads consistently next to `CRONOMETER_READ_ONLY` and
  `CRONOMETER_DATA_DIR`.
- **Default stays `stdio`.** Every existing client config keeps working unchanged.
- **Default host stays `127.0.0.1`.** Binding wider is an explicit choice the operator makes and
  the README names the consequence.

---

## 1. Transport selection in `main()`

**DO**
- Read `CRONOMETER_TRANSPORT`. Accept `stdio` (default when unset or empty) and
  `streamable-http`. Compare case-insensitively after stripping whitespace.
- Read `CRONOMETER_HOST` (default `127.0.0.1`) and `CRONOMETER_PORT` (default `8000`, must be
  an integer from 1 to 65535; Linux silently wraps 65536 to port 0).
- Read `CRONOMETER_ALLOWED_HOSTS` (comma-separated `Host` header values, `host:port` or
  `host:*`). When set, pass a `TransportSecuritySettings` with DNS-rebinding protection on, those
  hosts, and `http://`/`https://` origins derived from them. When unset on a non-loopback bind, log
  a warning that any `Host` is accepted. (Codex finding 1, 2026-10-24: the SDK auto-enables header
  checks only for loopback binds, so a `0.0.0.0` container is open to DNS rebinding otherwise.)
- For `streamable-http`, call `mcp.run(transport="streamable-http", host=host, port=port,
  transport_security=...)` (the last only when the allowlist is set).
- Bracket IPv6 hosts in the logged URL (`http://[::1]:8000/mcp`).
- For `stdio`, call `mcp.run(transport="stdio")` exactly as today.
- Log the transport, host and port at startup for `streamable-http` so a container log shows
  where the server is listening.
- On an unknown transport or a non-integer port, log one clear line naming the variable and the
  bad value, then exit non-zero.

**DON'T**
- Don't add `sse`. It is deprecated in the MCP spec and nobody asked for it.
- Don't expose further `run_streamable_http_async` options (stateless, json_response, path,
  session limits) as environment variables. Add them when someone needs them.
- Don't add authentication, TLS or a reverse proxy. Document the exposure instead (§3).
- Don't touch tool registration, the client lock or read-only mode. Transport is orthogonal.
- Don't add new dependencies to `pyproject.toml`.

**DONE WHEN**
- `main()` with no transport variables set calls `mcp.run(transport="stdio")`.
- `CRONOMETER_TRANSPORT=streamable-http` with no host or port set calls
  `mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)`.
- `CRONOMETER_HOST=0.0.0.0 CRONOMETER_PORT=9000` are passed through as `"0.0.0.0"` and `9000`
  (an `int`).
- `CRONOMETER_TRANSPORT=carrier-pigeon`, `sse`, and `CRONOMETER_PORT` of `abc`, `-1`, `0`,
  `65536` each exit non-zero without calling `mcp.run`; `1` and `65535` are accepted.
- `CRONOMETER_ALLOWED_HOSTS=nas.local:8000` on a `0.0.0.0` bind makes a request with
  `Host: evil.example:8000` get `421`, while `Host: nas.local:8000` initializes normally (live,
  not mocked).
- All of the above are covered by tests in `tests/test_server.py` that patch `server.mcp.run`,
  and `python -m pytest` passes.

## 2. Live smoke test

**DO**
- Start the server with `CRONOMETER_TRANSPORT=streamable-http CRONOMETER_PORT=<free port>` and
  dummy credentials, connect with the `mcp` Python client's streamable HTTP transport, and call
  `list_tools`.
- Confirm the same 27 tools (14 under `CRONOMETER_READ_ONLY=1`) come back as over stdio.

**DON'T**
- Don't call tools that hit Cronometer. `list_tools` proves the transport; the tools are already
  tested.

**DONE WHEN**
- `list_tools` over HTTP returns 27 tool names and the server shuts down cleanly on SIGINT.

## 3. README

**DO**
- Add a "Remote access (streamable HTTP)" subsection under *Configure your MCP client*, after
  *Read-only mode*, with: the three variables and their defaults, the endpoint URL form
  `http://<host>:<port>/mcp`, a minimal `docker run` example that passes credentials by
  `-e` and publishes the port, and a Claude Code `.mcp.json` snippet using `"type": "http"` and
  `"url"`.
- State plainly, in the same voice as the read-only caveat, that the endpoint has no
  authentication: anyone who can reach the port can read and change the Cronometer account.
  Keep it on localhost or behind something that authenticates (VPN, Tailscale, an authenticating
  reverse proxy).
- Mention that pairing it with `CRONOMETER_READ_ONLY=1` limits what a remote client can do, with
  the same caveat the read-only section already makes about the credentials themselves.

**DON'T**
- Don't ship a Dockerfile in this repo. The requester wants to write their own; a `docker run`
  line against the PyPI package is enough.
- Don't document `sse` or any option §1 chose not to expose.

**DONE WHEN**
- Every variable named in the README matches the names and defaults in `server.py`.
- The README example commands run as written (checked by eye against §2's working invocation).

## 4. Ship it

**DO**
- Run the Codex adversarial review on the unreleased diff (per project memory).
- Release `2.4.0` with `./release.sh 2.4.0` after Paul's go-ahead, with the `cphoskins` `gh`
  account active, then create the GitHub release from the tag so the publish workflow runs.
- Reply on discussion #12 with the version, the three variables and the localhost-by-default
  note, and ask the requester to confirm it works in their container.

**DON'T**
- Don't bump versions by hand or publish to the MCP Registry by hand (see `AGENTS.md`).
- Don't close the discussion until the requester has confirmed, or two weeks pass.

**DONE WHEN**
- `pip install cronometer-mcp==2.4.0` serves `/mcp` with `CRONOMETER_TRANSPORT=streamable-http`.
- Discussion #12 has the reply and a link to the release.

## Out of scope

- Authentication (OAuth, bearer tokens). Revisit if a second person asks or if someone exposes
  it publicly by mistake and reports it.
- `sse` transport, stateless mode, custom paths.
- A maintained Dockerfile or image.
