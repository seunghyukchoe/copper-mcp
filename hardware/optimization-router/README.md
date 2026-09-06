# Isolated external-router images

These are build recipes, not distributed images or a runtime configuration. An operator must
provide a locally resolved, digest-pinned base image to each build. The Python runner accepts only
the final digest-pinned image reference or immutable local image ID and never performs a pull.

- `freerouting/` wraps the documented DSN-to-SES command line as stdin-to-stdout bytes. It targets
  FreeRouting `2.2.4`, which the upstream project identifies as GPL-3.0 and supports DSN input and
  SES output. `JAVA_BASE_IMAGE` must provide Java 25; the build downloads the pinned release JAR
  and verifies its published SHA-256. Its entrypoint has a file/output ceiling and refuses an empty
  SES, disables analytics/API/GUI and discards diagnostics so stdout contains only SES.
- `simpleroutejson/` pins `@tscircuit/capacity-autorouter` to `0.0.872` (MIT). Its stdin/stdout
  protocol is a raw SimpleRouteJson adapter. Its transitive dependency resolution must be captured
  and reviewed in an operator-built image digest before it is configured for the runner. The
  entrypoint caps input/output at 16 MiB and solver steps at 1,000,000 with no payload diagnostics.

Neither image output is candidate authority. Docker flags in `container_runner.py` impose the
runtime containment; these Dockerfiles only establish fixed entrypoints and non-root ownership.
The `/work` tmpfs is private to UID/GID 65532, mode 0700, and cannot execute files.

## Build and smoke

Use the [local runtime setup](../../docs/integrations/local-router-runtime.md). Resolve base images
before building and supply their immutable digests as `JAVA_BASE_IMAGE` and `NODE_BASE_IMAGE`.
Build each corresponding subdirectory; do not send the full repository as Docker build context.
The local 2026-09-05 build used these public base digests:

```text
eclipse-temurin@sha256:10c251954d0bfe1a59ba93505f8c628d755919412400aa98685764c9353605d6
node@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
```

Run `scripts/smoke_optimization_routers.py` with the operator's absolute `--docker` executable,
local `--socket`, `--freerouting-image` and `--simpleroutejson-image` immutable IDs. It routes only
the committed original Apache-2.0 two-pad DSN and original synthetic two-pad SRJ input. It prints
redacted self-digesting execution/format metadata; raw output remains temporary. The corresponding
`external_router` pytest cases require all four `COPPER_MCP_TEST_DOCKER`,
`COPPER_MCP_TEST_DOCKER_SOCKET`, `COPPER_MCP_TEST_FREEROUTING_IMAGE`, and
`COPPER_MCP_TEST_SRJ_IMAGE` environment variables; missing configuration is an explicit skip.

The successful local smoke used these final image IDs:

```text
FreeRouting 2.2.4: sha256:e20ddc3f0f6b6fb5efd75dae62bb442894307bc16e11f7dd72892127a1e73130
SRJ 0.0.872:      sha256:23c2898e39083b5cb8b03e2a605d838065cabb3796ad62790e3bf3f1adae29ef
```

Both exited zero through `ContainerRouterRunner`; the shape checks found one SES wire and one SRJ
trace. The successful receipt digest was
`sha256:fe390ef7ccb5ac8707055213a10dcbfcf449ee7b091b82ce59a2d8dfd596c32f`.
This is a local execution/format smoke, not normalized candidate identity, connectivity proof,
KiCad DRC, held-out coverage, repeatability certification, a production bridge or apply authority.
The SRJ top-level package and final image are pinned; dependency-lock reproducibility remains open.

Sources: [FreeRouting v2.2.4](https://github.com/freerouting/freerouting/releases/tag/v2.2.4),
[fixed CLI options](https://github.com/freerouting/freerouting/blob/v2.2.4/docs/command_line_arguments.md),
[SRJ v0.0.872 package metadata](https://registry.npmjs.org/@tscircuit%2fcapacity-autorouter/0.0.872).
