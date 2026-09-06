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

## Local setup observation

On 2026-09-05 the authorized installation completed on the reference Mac: Colima 0.10.3, Docker CLI
29.8.0 and Docker Engine 29.5.2, Linux aarch64, four CPUs and 6 GiB VM memory. The engine answered
through the `copper-mcp` profile socket, and `default` remained the active Docker context. This is
runtime availability evidence, not external routing quality, general KiCad conversion, hosted CI
calibration or release acceptance. Exact router smoke evidence is recorded separately.
