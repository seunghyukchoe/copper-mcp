# ADR-0151: Operating-point numbers are not physics authority

- Status: Proposed; interpretation independently reviewed and locally validated
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0150](0150-confined-spice-source-preserves-declared-settings.md)

## Decision

Interpret one bounded ngspice 45.2 ASCII raw operating-point result before connecting a
confined simulator to the internally verified project export. Require the expected title,
backend command identity, complete voltage/current vector schema, real-valued plot and exactly
one point. Consume the entire result; reject extra plots, malformed or duplicate vectors,
missing values and non-finite numbers. Check byte, line, vector and time ceilings before
materializing larger intermediate structures. Keep results immutable and private.

Bind semantic identity to the expected backend, title, ordered vectors and exported values.
The native date is checked as bounded metadata but excluded from semantic identity. Preserve
exported decimal precision without depending on the process-global decimal context. Agreement
between repeated values proves reproducibility only, not convergence or physical accuracy.

This parser accepts supplied bytes and therefore authenticates neither the backend nor the
simulation. The production operation must still derive stimulus/load net bindings from declared
electrical inputs, retain exact captured models and native connectivity, use a fixed local
backend with bounded streams and confirmed cleanup, check diagnostics and convergence, and
recheck source freshness before delivery. No generic waveform framework, authority registry,
request-selected executable or model-generated command is introduced.

## Runtime research and remaining gates

An owned resistor-divider probe of the pinned local Linux image ran ngspice 45.2 successfully
using batch ASCII raw output. User initialization was suppressed and the standard initialization
directory contained an explicitly empty spinit. The fixed environment did not inherit user
settings; the container had no network or host mounts and was removed after execution.
The earlier `.print op` probe emitted warnings despite producing numbers; it is not the selected
result format. These probes are research, not the production confined executor or calibration.

Keep actual execution, explicit case/net/model bindings, convergence controls, asymmetric diode
checks and known-bad/incomplete cases mandatory. Calibrated SI/PI and the other physics domains,
engineering pass/fail decisions, human approval and live mutation remain separate requirements.
No five-area readiness requirement is closed by this interpretation layer alone.

Sources: [ngspice batch raw output](https://ngspice.sourceforge.io/ngspice-control-language-tutorial.html),
[ngspice documentation and environment variables](https://ngspice.sourceforge.io/docs.html).
The output shape and startup controls were checked against the actual pinned 45.2 runtime;
documentation for newer versions is not treated as compatibility evidence for that image.
