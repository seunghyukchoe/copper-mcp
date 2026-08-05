# KiCad session-revision PBKDF2 boundary

**Snapshot date:** 2026-08-05

## Question

How can the live KiCad route-preview CAS expose no offline-cheap fingerprint for an
instance-unique, limited-input API token while preserving bounded same-process operation?

## Official evidence

1. KiCad provides launched IPC plugins with `KICAD_API_TOKEN`, unique to the running KiCad
   instance, so it can detect an editor restart. It is a request credential and must not be
   published. [KiCad connection guidance](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/#connecting-to-kicad).
2. Python specifies `hashlib.pbkdf2_hmac(hash_name, password, salt, iterations, dklen=None)` as
   PBKDF2 using HMAC. Its documentation says password-hashing functions need a salt and tunable
   slowness, recommends salt from a proper random source with at least 16 bytes, and suggests
   hundreds of thousands of SHA-256 iterations as of 2022.
   [Python `hashlib.pbkdf2_hmac`](https://docs.python.org/3/library/hashlib.html#hashlib.pbkdf2_hmac).
3. CodeQL's `py/weak-sensitive-data-hashing` recommendation names PBKDF2 for passwords and other
   limited-input data. Its [official query source](https://github.com/github/codeql/blob/main/python/ql/src/Security/CWE-327/WeakSensitiveDataHashing.ql)
   distinguishes computationally expensive hash functions; the
   [query help](https://codeql.github.com/codeql-query-help/python/py-weak-sensitive-data-hashing/)
   explains why SHA-256 alone is insufficient for limited-input secrets.
4. Python documents `hmac.compare_digest()` for digest verification without content-dependent
   timing behavior. [Python `hmac`](https://docs.python.org/3/library/hmac.html).

## Current contract

At process initialization CopperMCP generates one non-persistent 32-byte salt with
`secrets.token_bytes(32)`. For a validated token it derives a 32-byte value with a fixed,
CPU-only PBKDF2-HMAC-SHA256 work factor:

```text
pbkdf2-hmac-sha256:<PBKDF2-HMAC-SHA256(
  token,
  "copper-mcp:kicad-ipc-session-revision:v2\\0" || process_salt,
  iterations=200000,
  dklen=32
)>
```

The literal wire prefix makes the algorithm migration explicit. Only lowercase 64-hex values of
that type are accepted. CAS comparisons continue to use `hmac.compare_digest()` after format
validation. The salt is neither serialized nor logged; a fresh process generates a fresh salt, so
old caller expectations deliberately fail closed. This is a narrow same-process freshness signal,
not remote authentication or persistent session identity.

## Cost selection and local measurement

The fixed 200,000 iteration count falls within Python's documented “hundreds of thousands”
guidance while avoiding a caller-controlled work factor. On the development host, after two
warmups, seven one-derivation samples over a non-secret placeholder token measured **53,615,042 to
54,357,375 ns**, with a **53,915,333 ns median**. This is a local CPU observation, not a latency
guarantee: CI hosts, OpenSSL builds, scheduling, and load vary. The regression fixes the work
setting and applies a deliberately broad 5-second one-derivation guard against accidental
unbounded work.

## Regression evidence and non-claims

Focused fake-IPC tests prove exact prefix/hex format, deterministic same-process results,
distinct-token output, process-salt rotation refusal before Board IR conversion, legacy HMAC and
unkeyed-SHA refusal, no token in successful output, schema alignment, fixed work settings, and the
broad runtime guard. The construction does not protect process memory, authenticate a caller,
prevent KiCad's documented ABA possibility, hard-preempt synchronous IPC, prove real-editor
behavior, or authorize DRC/editor mutation/fabrication.
