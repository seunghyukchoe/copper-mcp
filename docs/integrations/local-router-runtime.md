# Local external-router runtime

External routers run in a dedicated local Linux VM. The operator installs and starts the runtime;
MCP arguments cannot select an executable, daemon endpoint, image, shell command or mount.
See the [native optimization limitations](native-optimization.md) and the
[router image recipes](../../hardware/optimization-router/README.md).

## macOS setup

The approved local setup uses Homebrew Colima and the Docker CLI:

```sh
brew install colima docker
colima start copper-mcp --activate=false --cpus 4 --memory 6 --disk 30 \
  --runtime docker --vm-type vz --mount none --ssh-agent=false \
  --ssh-config=false --port-forwarder none
docker --context colima-copper-mcp info
```

This creates a separate `copper-mcp` profile, without switching the current Docker context,
sharing the host home directory, forwarding the SSH agent or enabling Kubernetes. Starting at
login is not configured. The 30 GiB data disk is a capacity limit, not its initial occupied size;
Colima also maintains its VM root disk and downloaded image cache.

For manual operation:

```sh
colima start copper-mcp --activate=false
colima status copper-mcp
colima stop copper-mcp
```

The runner uses the profile's explicit local Unix socket and an empty private Docker client
configuration, not the ambient active context or credential settings. Builds may download public
dependencies. Routing execution uses already-installed immutable image IDs, no network, no host
mounts, a read-only root filesystem, a non-root user, bounded temporary storage and output, and
fixed resource ceilings. Returned router bytes remain untrusted until geometry normalization,
candidate validation and KiCad DRC succeed. A working container does not establish those gates.

## Server configuration

Set these in the server's operator environment, not in `start_optimization` arguments:

| Variable | Required value |
|---|---|
| `COPPER_MCP_KICAD_CLI` | Absolute path to the reviewed KiCad CLI. |
| `COPPER_MCP_OPTIMIZATION_DOCKER` | Absolute path to the installed Docker executable. |
| `COPPER_MCP_OPTIMIZATION_DOCKER_SOCKET` | Absolute path to the dedicated local Unix socket. |
| `COPPER_MCP_OPTIMIZATION_DOCKER_CONFIG_ROOT` | Absolute path to a private Docker client configuration root. |
| `COPPER_MCP_OPTIMIZATION_FREEROUTING_IMAGE` | Installed immutable image ID/digest for the pinned FreeRouting recipe; required only when selected. |
| `COPPER_MCP_OPTIMIZATION_SPECCTRA_PYTHON` | Absolute path to the reviewed KiCad-bundled Python providing `pcbnew`; required only for FreeRouting. |
| `COPPER_MCP_OPTIMIZATION_SRJ_IMAGE` | Installed immutable image ID/digest for the pinned SRJ recipe; required only when selected. |

Use the image identities emitted by the reviewed local build, never mutable tags. The runtime
must already be started; optimization does not install or start Docker, download images, expose
the host filesystem, or add network access. A single external-backend request refuses missing
configuration before placement work. A hybrid request records an unavailable backend and may
continue only to another explicitly declared backend, within the same remaining allocation.
Changes to the selected executable, image, socket identity or runtime configuration invalidate
the captured request. Unused backend settings do not affect its identity.

## Local setup observation

On 2026-09-05 the authorized installation completed on the reference Mac: Colima 0.10.3, Docker CLI
29.8.0 and Docker Engine 29.5.2, Linux aarch64, four CPUs and 6 GiB VM memory. The engine answered
through the `copper-mcp` profile socket, and `default` remained the active Docker context. This is
runtime availability evidence, not external routing quality, general KiCad conversion, hosted CI
calibration or release acceptance. Exact router smoke evidence is recorded separately.
