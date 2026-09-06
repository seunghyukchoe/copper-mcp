# Operator supplies a locally resolved, digest-pinned Java 25 image.
# Release digest: https://api.github.com/repos/freerouting/freerouting/releases/tags/v2.2.4
ARG JAVA_BASE_IMAGE
FROM ${JAVA_BASE_IMAGE}

ADD https://github.com/freerouting/freerouting/releases/download/v2.2.4/freerouting-2.2.4.jar /opt/freerouting/freerouting-2.2.4.jar
RUN printf '%s  %s\n' f5ed374182900ccc78e473518bbb9f6b869f4a07159495f663a76f52bb10523b /opt/freerouting/freerouting-2.2.4.jar | sha256sum -c - \
    && chmod 0444 /opt/freerouting/freerouting-2.2.4.jar

USER 65532:65532
WORKDIR /work
COPY --chown=65532:65532 entrypoint.sh /opt/copper-mcp/entrypoint.sh
RUN chmod 0555 /opt/copper-mcp/entrypoint.sh
ENTRYPOINT ["/opt/copper-mcp/entrypoint.sh"]
