# The operator supplies a local, digest-pinned Node image. Build-time npm access is operator action.
ARG NODE_BASE_IMAGE
FROM ${NODE_BASE_IMAGE}

WORKDIR /opt/copper-mcp
COPY package.json ./
RUN npm install --ignore-scripts --omit=dev --package-lock=false
COPY --chown=65532:65532 entrypoint.mjs ./entrypoint.mjs
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["node", "/opt/copper-mcp/entrypoint.mjs"]
