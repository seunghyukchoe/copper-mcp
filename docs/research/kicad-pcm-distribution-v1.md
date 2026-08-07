# Distributing the KiCad IPC observer through the Plugin and Content Manager

Research basis for [issue #98](https://github.com/seunghyukchoe/copper-mcp/issues/98), reviewed
2026-08-06. It records the package format as the **schema and the submission CI actually define
it** — not as the prose describes it, because on six points the two disagree — the two KiCad
behaviours that decide whether a Python IPC plugin is reachable at all, and what widening the
install base does and does not change about the live-IPC boundary.

Everything here was read from a primary source on 2026-08-06 and each claim carries the URL it came
from. Nothing in this note is reconstructed from memory.

## 1. Where the format is actually defined

Three documents govern a package, and they are not equally authoritative:

1. **The JSON Schema** — <https://go.kicad.org/pcm/schemas/v1> and
   <https://go.kicad.org/pcm/schemas/v2>. These short links are what KiCad's own submission CI
   re-downloads at validation time, which makes them the authority rather than any copy. On
   2026-08-06 they resolved into the KiCad **source tree**, not the addons repository:
   <https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/pcm/schemas/pcm.v1.schema.json> and
   <https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/pcm/schemas/pcm.v2.schema.json>. Both
   are vendored verbatim under [`schemas/kicad-pcm/`](../../schemas/kicad-pcm/README.md) with their
   digests.
2. **The submission CI** — `ci/validate/package.py`, `ci/validate/image.py`, and `ci/validate.sh`
   in <https://gitlab.com/kicad/addons/metadata>. This enforces a substantial set of rules the
   schema does not express at all: an archive-contents whitelist, an in-archive/submitted metadata
   cross-check, size tolerances, and immutability of published versions. A package can validate
   against the schema and still be rejected.
3. **The prose guide** — <https://dev-docs.kicad.org/en/addons/>. Authoritative for policy
   (namespacing, licensing, the submission workflow) and for the archive folder trees. Not
   authoritative for field constraints.

### The prose and the schema disagree

| Field | dev-docs says | Schema enforces | What we did |
|---|---|---|---|
| `description` | "maximum of 150 characters" | `maxLength: 500` | Kept to 141. A reviewer reads the prose. |
| `identifier` | "alphanumeric and dash only", 2–50 chars | `^[a-zA-Z][-a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$` — dots allowed, up to 100 | 48 chars, reverse-DNS. |
| `resources` | "(optional)" | listed in `required` | Present. |
| `author.contact` | "An optional `contact` field" | `Contact` requires `name` **and** `contact` | Both present. |
| `version` | "format of this is up to you" | `^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$` | `0.6.0`. |
| `kicad_version` | "(major.minor)" | `^\d{1,2}(\.\d{1,2}(\.\d{1,2})?)?$` — up to three parts | `9.0.1`, see §4. |

The identifier row is the sharpest: the same page forbids dots and then mandates reverse-DNS
namespacing with the dotted example `com.github.username.packagename`. The schema settles it.

Both schemas declare `$ref: "#/definitions/Package"` at the root, so a package `metadata.json`
validates against the whole document with no pointer. The same file also defines `Repository`,
`RepositoryResource`, and `PackageArray`, which describe the repository index (`repository.json`,
`packages.json`) rather than a package. CopperMCP publishes no repository index and uses neither.

## 2. v1 and v2, and why we validate against both

Two schema versions are published. dev-docs: v1 is KiCad 6.0+, v2 is KiCad 10.0+, and "New
packages should target the v2 schema." The official repository publishes `repository.json` /
`packages.json` (v2) alongside `repository-v1.json` / `packages-v1.json`; KiCad 10.0+ sends
`Accept: application/vnd.kicad.pcm.v2+json` and older clients get the v1 files. A package whose
`type` exists in v1 — `plugin`, `library`, `colortheme` — appears in **both** lists.

Ours is `type: "plugin"`, so it is served to KiCad 6.0 through 10.x alike, and both schemas have to
hold. v1 is the stricter of the two, which is the useful direction: v1 closes `type` to a
three-member enum and `license` to a fixed list of roughly 100 Debian-style strings, where v2
accepts any lowercase-alphanumeric type and any non-empty licence string.

Validating against both is not caution, it is what the submission CI does. `ci/validate.sh`
downloads both schemas from the two short links and passes both to the validator, which applies v2
unconditionally and then applies v1 to any package whose type is in `V1_TYPES` or is
down-convertible:

```python
jsonschema.validate(metadata, SCHEMA_V2)
...
if pkg_type in V1_TYPES or pkg_type in V1_DOWN_CONVERT:
    v1_metadata = down_convert_v1(metadata)
    ...
    jsonschema.validate(v1_metadata, SCHEMA_V1)
```

Down-conversion is a no-op for `plugin`, so the document our tests check against v1 is the same
document upstream checks.

The licence list is **not SPDX**. It contains `Apache-2.0`, `GPL-3.0`, `MIT`, `CERN-OHL`,
`WTFPL`, `Unlicense`, and the two escape hatches `open-source` and `unrestricted`. SPDX-only
spellings such as `GPL-3.0-only` are absent and would fail v1. `Apache-2.0` is a literal member,
so CopperMCP's licence needs no translation.

Separately, the repository's policy requires code packages to be under a licence **compatible with
the GNU GPL**. Apache-2.0 is one-way compatible with GPL-3.0 and is *not* compatible with GPL-2.0.
That is fine here — the requirement is compatibility for redistribution alongside KiCad, and the
plugin is not a derivative work of KiCad — but it is the reason a permissive-but-incompatible
licence would have needed a conversation before submission rather than after.

## 3. The two KiCad behaviours that decide whether the plugin works at all

These are not in the packaging documentation. Both were read from
<https://gitlab.com/kicad/code/kicad/-/raw/master/common/api/api_plugin_manager.cpp> and both
changed the package.

### A Python IPC plugin without `requirements.txt` is invisible

`API_PLUGIN_MANAGER::ReloadPlugins` walks the stock path, the PCM third-party path
(`KICADn_3RD_PARTY`), and the user path with a traverser that fires on **any** file named
`plugin.json`, at any depth. Discovery is therefore not sensitive to where inside the installed
package the manifest sits.

Readiness is. `processPluginDependencies` marks a non-Python plugin ready immediately, but queues a
`CREATE_ENV` or `INSTALL_REQUIREMENTS` job for every Python one. The install job looks for
`requirements.txt` beside the manifest and, when it is not readable, reports
`requirements.txt could not be read` and stops. `m_readyPlugins.insert` is reached **only** on a
pip exit status of 0. Both `GetActionsForScope` and `InvokeAction` skip plugins outside that set:

```cpp
if( !m_readyPlugins.count( plugin.Identifier() ) )
{
    wxLogTrace( traceApi, ... "Plugin %s is not ready" ... );
    return -1;
}
```

So a Python IPC plugin shipped without `requirements.txt` installs, discovers, validates against
KiCad's manifest schema — and then never appears in the toolbar or the menu, with the reason only
in a trace log. The file is mandatory.

### …and it cannot be used to install CopperMCP

The install job runs, verbatim:

```
pip install --no-input --isolated --only-binary :all: --require-virtualenv --exists-action i -r requirements.txt
```

That resolves against PyPI, and `copper-mcp` is deliberately not published to PyPI — publication is
blocked pending package ownership, trusted publishing, and a supply-chain review
([`docs/releasing.md`](../releasing.md)). A `requirements.txt` naming it would make pip exit
non-zero, which produces exactly the same outcome as the missing file: the action never becomes
reachable.

The resolution is in the third line of the same file. The environment is created with

```cpp
std::vector<wxString> args = { "-m", "venv", "--system-site-packages", job.env_path };
```

`--system-site-packages` means whatever is installed in the interpreter configured under
*Preferences → Plugins* is importable from the plugin environment. So the package ships a
`requirements.txt` that exists and installs nothing — a comments-only file — and the operator
installs `copper-mcp[kicad]` into that interpreter themselves. Both halves of that are asserted by
[`tests/test_pcm_package.py`](../../tests/test_pcm_package.py), because either one alone is a
silent failure.

The gap has a consequence for the entrypoint. A PCM install delivers the plugin file and not
CopperMCP, so `from copper_mcp.kicad_ipc import ...` at module scope would put an unhandled
`ImportError` — and its filesystem path — into KiCad's warning bar on a new user's first click.
The import is now inside `main()` behind a fixed, actionable sentence.

### What KiCad hands the launched process

`InvokeAction` launches the entrypoint as a standalone process and injects the endpoint into its
environment:

```cpp
if( Pgm().ApiServerOrNull() )
{
    env.env[wxS( "KICAD_API_SOCKET" )] = Pgm().GetApiServer().SocketPath();
    env.env[wxS( "KICAD_API_TOKEN" )] = Pgm().GetApiServer().Token();
}
```

`KICAD_API_TOKEN` is therefore present in every launched plugin's environment whether the plugin
wants it or not. Keeping it there is the property SEC-121 reviews.

## 4. Field decisions, and why each one is the value it is

- **`identifier: com.github.seunghyukchoe.coppermcp-live-observer`.** The official repository
  requires reverse-DNS namespacing tied to the hosting service — "official KiCad addons use the
  namespace `org.kicad.packagename`… `com.github.username.packagename`" — and the package
  directory name in the metadata MR must equal this string. It is not the same identifier as the
  KiCad **API** manifest's `org.coppermcp.live-observer` in `plugin.json`; those are separate
  namespaces with separate patterns, and the API one is already pinned by an existing test.
- **`type: "plugin"`, `runtime: "ipc"`.** `runtime` is the field that distinguishes an IPC plugin
  from a legacy SWIG one, and the schema comment is explicit that it is "Assumed to be swig if
  absent."
- **`kicad_version: "9.0.1"`.** dev-docs dates `runtime` to "since KiCad 9.0.1". A 9.0.0 client
  reading this package would ignore the field, assume SWIG, and try to load an IPC plugin as a
  legacy one. Declaring the exact release that understands the field is more honest than `9.0`,
  and the schema's three-component pattern permits it.
- **No `kicad_version_max`.** A maximum we cannot currently justify would silently hide the package
  from a future KiCad. The repository maintainers set this field themselves when a package is
  found broken, which is the right mechanism for it.
- **No `platforms`.** The validator defaults an absent list to all three and then requires the
  in-archive and submitted copies to agree, so absence on both sides is the simplest correct
  answer for a pure-Python plugin.
- **`status: "testing"`.** The repository's version-immutability rules let `stable` move only to
  `deprecated`, and `deprecated` is terminal. `testing` is the honest description of a
  first-published observation smoke test and is the only starting value that can still be walked
  back.

## 5. Archive layout, enforced as a whitelist

dev-docs gives the folder tree for a Python plugin:

```
Archive root
|- plugins/
|- resources/
   |- icon.png
|- metadata.json
```

`ci/validate/package.py` turns that into a whitelist applied to **every** ZIP entry — anything
unmatched fails with `package contains extra file "…"`:

```python
ALLOWED_FILES = {
    "all":    ["/metadata.json", "/resources/icon.png"],
    "plugin": ["/plugins/*", "/plugins/**/*"],
    ...
}
```

Two consequences worth naming. A root-level `LICENSE` would be rejected, so the Apache-2.0
section 4(a) obligation to give recipients a copy of the licence is met at `plugins/LICENSE`, which
the `/plugins/*` pattern admits. And dev-docs adds that the plugin goes *directly* in `plugins/`,
not one directory deeper.

Directory entries are skipped by the validator (`if entry.is_dir(): continue`), so the build writes
none — each one omitted is a stored mode and timestamp that cannot drift.

**Icon** (`ci/validate/image.py` defaults): PNG only — the validator passes
`formats=["PNG"]` — with width **and** height at most 64, and a file size of at most 20480 bytes.
64×64 is a maximum rather than a requirement, and the icon is optional entirely. Ours is 64×64 and
872 bytes.

## 6. The in-archive metadata and the submitted metadata are different documents

This is the rule most likely to be got wrong by hand, and it is why the build is mechanical.
dev-docs states it directly:

> The `download_*` keys must only be present in the version of the `metadata.json` that you submit
> to the package metadata repository, not in the version of the file that is actually present in
> the package archive. It is not possible to put a valid `download_sha256` value in the
> `metadata.json` file inside the archive.

`validate_packaged_metadata` then downloads the archive and cross-checks the two copies: identical
`identifier`, **exactly one** version object in the archive's copy, no `download_sha256` in it, and
field-by-field equality — present-or-absent as well as equal — on `status`, `kicad_version`,
`kicad_version_max`, `download_url`, and `platforms`.

Meanwhile `validate_metadata` requires the submitted copy to carry `download_url` (must begin with
`http`), `download_size`, `install_size`, and `download_sha256` on **every** version:
"download sha256 is required".

Sizes are compared with a 1024-byte tolerance, and `install_size` is defined operationally as the
sum of uncompressed entry sizes:

```python
instsize += entry.file_size
```

Downloads are capped at `MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024`.

Published versions are then **immutable**: `version_epoch`, `download_sha256`, `download_size`, and
`install_size` can never change for a version already in the repository. Re-validation is triggered
only for a new version or a changed `download_url`. That immutability is the real argument for
building the archive reproducibly rather than the convenience one — a rebuild that produced
different bytes for a published version would be unfixable in place.

An incidental upstream bug, recorded because it bounds what submission proves rather than what we
should do: in `validate_version` the archive's own metadata is passed to
`jsonschema.validate(metadata, SCHEMA_V2)` using the outer submitted document, not the copy just
parsed out of the ZIP. The in-archive metadata therefore never receives a direct schema check
upstream. The field-by-field comparison catches most of what that would have caught, but it is a
reason to validate the packaged copy ourselves, which
[`tests/test_pcm_package.py`](../../tests/test_pcm_package.py) does against both schemas.

## 7. Reproducibility, and the exact scope of the claim

[`scripts/build_pcm_package.py`](../../scripts/build_pcm_package.py) fixes every ZIP field that
could record when or where a build ran: the 1980-01-01 ZIP epoch as the modification time, mode
0644, host system Unix, empty comment and extra fields, and one declared member order asserted to
be sorted rather than a directory walk.

Members are **stored, not deflated**. That is the decision worth stating. A deflated archive is
reproducible only for a fixed zlib build; a stored one is a pure function of the member names,
their bytes, and their order, for any implementation that honours the fields above. The archive is
about 20 KB either way — most of it the Apache-2.0 licence text — so the stronger property costs
nothing worth having. ISO 21320-1, which dev-docs names as the required ZIP profile, permits
stored entries.

Verified: byte-identical across two Python versions (3.12.13 and 3.14.2), two timezones, and
different `PYTHONHASHSEED` values, at `download_sha256`
`64b70f419e042523edef25562ad2b16bc62471de16b49d704f0cfbf5229bd4f7` for `0.6.0`. The test suite
re-checks this by building twice in-process and once in a subprocess with `TZ`, `PYTHONHASHSEED`,
and `SOURCE_DATE_EPOCH` all perturbed.

The hash and both sizes are measured from the artifact the script just produced and written into
the submission metadata, so no one transcribes a digest by hand.

## 8. Submission — what a human must do, and what is deliberately not automated

The metadata merge request is a publishing action against a third party's repository under a real
person's GitLab account, and it carries policy attestations (maintainer identity, licensing,
content policy) that only the maintainer can make. It is therefore prepared here and executed by a
human; the checklist lives in
[`hardware/kicad-ipc-plugin/README.md`](../../hardware/kicad-ipc-plugin/README.md).

The mechanics, from dev-docs and the metadata repository's own README:

- Fork <https://gitlab.com/kicad/addons/metadata>. Add `packages/<identifier>/metadata.json` plus
  an optional `icon.png`. The directory name **must** equal the `identifier` field.
- Work on a branch that is **not** `main` — `validate-push` diffs against `target/main`, and the
  README warns that "some steps of the validation will not work as intended" otherwise.
- The `build` CI stage prints a temporary PCM repository URL that can be added to KiCad to test the
  package before the merge request is reviewed.
- Never open a merge request against <https://gitlab.com/kicad/addons/repository>. Its README says
  "Do not send merge requests for packages here"; it is regenerated from the metadata repository.
- After merge, a scheduled job propagates to the public repository, taking up to a day.
- `tools/packager.py` in the metadata repository runs most of the CI checks locally.

Policy gates that apply to this package specifically: the download URL must be publicly accessible
before submission, which ties it to a published GitHub release; the source must be hosted somewhere
with issue tracking; metadata must be in English; and the reverse-DNS namespace must be tied to the
hosting service or a domain the submitter controls. The commercial-services clause does not apply —
CopperMCP connects to no service.

## 9. What this changes about the security boundary

Recorded in full as SEC-121. In short: PCM distribution changes the *population* that can install
the observation half of the ADR-0069 boundary, and changes nothing about what that half can do.

- The server half stays default-off. `COPPER_MCP_ALLOW_LIVE_IPC` is unchanged, still exact
  `{"0", "1"}`, still read from the environment KiCad launched the plugin in. Nothing in the
  archive can set it, and the packaged code imports from a closed three-name list that contains no
  `os`, so it cannot write an environment variable at all.
- The `KICAD_API_TOKEN` KiCad injects into the plugin process stays there. It is never passed to
  `kipy`, never serialized, never printed. The packaged code contains no reference to the name in
  any string literal, identifier, or attribute — checked against the parsed tree rather than the
  file bytes, so the two places the name appears as *prose* do not have to weaken the check.
- The failure mode a wider audience actually meets — CopperMCP not installed — was a traceback
  disclosing a filesystem path, and is now a fixed sentence.
- What a PCM listing adds is *reach*, and the mitigation for reach is disclosure. The
  `description` and `description_full` shown in the PCM dialog state the default-off posture, the
  flag name, the read-only guarantee, and the token property before anyone installs anything.

## Sources

All retrieved 2026-08-06.

- <https://dev-docs.kicad.org/en/addons/> — package format, folder trees, field guide, submission
  process, licensing and content policy, schema versioning.
- <https://go.kicad.org/pcm/schemas/v1> and <https://go.kicad.org/pcm/schemas/v2> — the schemas,
  vendored under [`schemas/kicad-pcm/`](../../schemas/kicad-pcm/README.md).
- <https://gitlab.com/kicad/addons/metadata> — submission target; `ci/validate/package.py`,
  `ci/validate/image.py`, `ci/validate.sh`, `ci/build-repository.py`, `tools/packager.py`.
- <https://gitlab.com/kicad/addons/repository> — the generated public repository; do not submit
  here.
- <https://gitlab.com/kicad/code/kicad/-/raw/master/common/api/api_plugin_manager.cpp> — plugin
  discovery, readiness, the venv and pip invocations, and the launch environment.
- <https://gitlab.com/kicad/code/kicad/-/raw/master/kicad/pcm/pcm.cpp> — client-side archive hash
  verification.
- <https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/> — IPC plugins run as standalone
  processes told the socket path and instance identifier through environment variables.

## What this note does not establish

No claim is made that the package has been submitted, accepted, or published: it has not, and
submission is a human step by design. No claim is made about how KiCad behaves on Windows or macOS
beyond what the cited source says, because the package has not been installed from a live PCM
repository on any platform as part of this work. The reproducibility claim is about this build
script's output, not about the toolchain that produces the wheel and sdist beside it.
