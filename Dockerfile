# CopperMCP MCP server.
#
# The image carries the server and nothing else. It deliberately does NOT bundle KiCad:
# board inspection, DRC, ERC, and rendering all delegate to an authoritative `kicad-cli`,
# and shipping a KiCad build inside this image would let a caller believe those surfaces
# work when the host's own KiCad is what must answer for them. Without KiCad present the
# server starts, lists every tool, and refuses the KiCad-backed ones with their normal
# typed diagnostics — which is the honest behaviour, not a degraded one.
#
# Nothing here enables a mutation surface. COPPER_MCP_ALLOW_APPLY, COPPER_MCP_ALLOW_LIVE_IPC
# and COPPER_MCP_ALLOW_LIVE_APPLY are all unset, so the image is read-only by default and an
# operator must opt in explicitly at run time, exactly as on a host install.
FROM python:3.12-slim AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip build \
 && python -m build --wheel --outdir /dist

FROM python:3.12-slim

# A non-root user with no write access to the installed package. The server only ever needs
# to read the workspace it is given.
RUN useradd --create-home --uid 10001 copper \
 && mkdir -p /workspace \
 && chown copper:copper /workspace

COPY --from=build /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl \
 && rm -f /tmp/*.whl

USER copper
WORKDIR /workspace

# The workspace is the only directory the server will read boards from; mount yours over it.
ENV COPPER_MCP_WORKSPACE=/workspace \
    PYTHONUNBUFFERED=1

# stdio transport: the container speaks MCP on stdin/stdout, so run it with `docker run -i`.
ENTRYPOINT ["copper-mcp-server"]
