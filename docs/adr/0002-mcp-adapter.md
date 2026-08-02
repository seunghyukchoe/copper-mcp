# ADR-0002: MCP is an external adapter

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

MCP is useful for agent interoperability but evolves faster than PCB geometry contracts. The router
must also support CLI, KiCad, tests, training environments, and non-MCP applications.

## Decision

MCP handlers call pure application services. The domain and routing layers do not import MCP types.
Durable jobs exist independently of protocol sessions, and MCP Tasks is a progressive enhancement
rather than the only lifecycle interface.

## Consequences

Protocol upgrades remain localized and the core is reusable. Some schemas are represented at both
the application and MCP boundaries and require compatibility tests.

## Alternatives considered

- MCP-native domain objects: rejected because it couples geometry and storage to one transport.
- A model-provider-specific API: rejected because it harms openness and local operation.
