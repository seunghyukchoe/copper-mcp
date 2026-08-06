# CopperMCP KiCad IPC observer

This is an official KiCad IPC Python-plugin shape for the optional live observer. It
registers one PCB-editor action and prints only a digest, version metadata, byte count,
and bounded object counts. It does not call `begin_commit`, `push_commit`, `update_items`,
`create_items`, `remove_items`, or any other mutation API.

## What installing this does and does not grant

**It grants nothing on its own.** The plugin is the observation half of a boundary whose server
half is off by default
([ADR-0069](../../docs/adr/0069-operator-gated-live-ipc-observation.md)). The action reads a board
only when both of these are true:

1. CopperMCP is installed in the Python interpreter KiCad is configured to use, and
2. the environment KiCad was launched from sets `COPPER_MCP_ALLOW_LIVE_IPC=1`, a value that must be
   exactly `0` or `1` — `true`, `yes`, and an empty string are configuration errors, not silent
   enables.

With the flag unset the action prints `CopperMCP IPC observer unavailable: KicadIpcDisabledError`
and reads nothing at all; it does not fall back to discovering a socket. Clicking the button is an
operator action, but the flag is what makes it one the *host* has authorized, and it is the same
switch for every live surface of the MCP server. Editor mutation sits behind a second, separate
opt-in and is not reachable from here.

`KICAD_API_TOKEN`, which KiCad puts in the environment of every launched IPC plugin, stays inside
the plugin process. It is never passed to the `kicad-python` binding, never written to the output,
and never transmitted anywhere.

## Install from the Plugin and Content Manager

Once the package is published to the official KiCad addon repository, this is the whole install:

1. **Tools → Plugin and Content Manager**, search for *CopperMCP Live Observer*, install, apply.
2. Install CopperMCP into the interpreter KiCad uses for plugins, shown under
   **Preferences → Plugins**:

   ```sh
   python -m pip install 'copper-mcp[kicad]'
   ```

   The PCM cannot do this for you. KiCad resolves a plugin's `requirements.txt` against PyPI with
   `--only-binary :all:`, and CopperMCP is deliberately not published to PyPI. KiCad creates the
   per-plugin environment with `--system-site-packages`, so an install into that interpreter is
   importable from the plugin. Until you do this, the action refuses with a message naming the
   step rather than raising.
3. Enable the IPC API server in **Preferences → Plugins** and restart KiCad.
4. Set `COPPER_MCP_ALLOW_LIVE_IPC=1` in the environment KiCad is launched from. Setting it in a
   shell after KiCad has started has no effect on the already-running process.

The package is `com.github.seunghyukchoe.coppermcp-live-observer`, requires KiCad 9.0.1 or newer,
and is published with `status: testing` — it is a read-only observation smoke test, not placement
or routing apply.

## Development install

The plugin directory is hardware-side integration code and is intentionally not included in
the Python wheel. Copy both files into the PCB Editor plugin discovery directory configured by
your KiCad installation; `plugin.json` must be at the root of the copied plugin directory. For
example, after choosing an absolute discovery directory from KiCad's add-on documentation:

```sh
export KICAD_PCB_PLUGIN_DIR='/absolute/path/to/kicad/pcb-plugin-discovery'
mkdir -p "$KICAD_PCB_PLUGIN_DIR/org.coppermcp.live-observer"
cp plugin.json coppermcp_ipc_plugin.py pcm/requirements.txt \
  "$KICAD_PCB_PLUGIN_DIR/org.coppermcp.live-observer/"
```

`requirements.txt` is copied along with the two plugin files because KiCad refuses to mark a Python
IPC plugin *ready* without it: the action registers, validates, and then never appears in the
toolbar. The file deliberately installs nothing.

Do not point KiCad at the repository root unless that is the configured discovery directory. The
copy step is required for the manifest and action to be discoverable; installing the Python
package alone does not register a KiCad plugin.

Install CopperMCP and its optional binding into the Python environment KiCad creates for
the plugin:

```sh
python -m pip install 'copper-mcp[kicad]'
```

For a source checkout, use an editable install instead:

```sh
python -m pip install -e '/absolute/path/to/12_PCB[kicad]'
```

KiCad 9/10 must be running with the IPC server enabled. `kicad-python` supplies the
official socket/token client; CopperMCP refuses non-local endpoints and refuses a newer
KiCad than the installed binding by default.

## Building the PCM package

```sh
python scripts/build_pcm_package.py
```

This writes `dist/coppermcp-live-observer-<version>.zip` and, beside it, the repository-side
`coppermcp-live-observer-<version>.metadata.json` — the same document with `download_url`,
`download_sha256`, `download_size`, and `install_size` measured from the archive it just built. The
archive is byte-reproducible: members are stored rather than deflated, written in one declared
order, with the ZIP epoch as every timestamp, so the same source always yields the same digest and
nobody transcribes a hash. The release workflow runs the same command and the archive is attested
alongside the wheel and sdist.

The two metadata documents are deliberately different, because KiCad's submission CI requires it:
the copy inside the archive must carry exactly one version and must **not** carry
`download_sha256`, while the submitted copy must carry all four download fields. See
[the PCM distribution research note](../../docs/research/kicad-pcm-distribution-v1.md).

## Submitting to the official KiCad addon repository

**This is a human step and is deliberately not automated.** It is a merge request against a third
party's repository under a real GitLab account, and it carries attestations — maintainer identity,
licensing, content policy — that only the maintainer can make. Everything up to it is prepared by
the release workflow.

Do it only after the GitHub release is published, because a version merged into the KiCad
repository is immutable: `download_sha256`, `download_size`, and `install_size` can never change
for it afterwards.

- [ ] The GitHub release `vX.Y.Z` exists and `coppermcp-live-observer-X.Y.Z.zip` downloads
      publicly, unauthenticated, from the `download_url` in the sidecar metadata.
- [ ] `python scripts/build_pcm_package.py --expect-version vX.Y.Z --no-write` prints a
      `download_sha256` equal to the digest of the downloaded asset.
- [ ] `PYTHONPATH=src python -m pytest tests/test_pcm_package.py` passes on the release commit.
- [ ] Fork <https://gitlab.com/kicad/addons/metadata> and create a branch that is **not** `main`.
      The repository's own README warns that validation does not work as intended from `main`,
      because the CI diffs against `target/main`.
- [ ] Create `packages/com.github.seunghyukchoe.coppermcp-live-observer/`. The directory name must
      equal the `identifier` field exactly.
- [ ] Copy the release's `coppermcp-live-observer-X.Y.Z.metadata.json` in as `metadata.json`, and
      `hardware/kicad-ipc-plugin/pcm/icon.png` in as `icon.png`. For an **update**, add the new
      version object to the existing `versions` array rather than replacing it, and change no
      field of any already-published version.
- [ ] Optionally run `tools/packager.py` from the metadata repository, which reproduces most of the
      CI checks locally.
- [ ] Push the branch and wait for the `validate` stage. Then read the `build` stage log: it prints
      a temporary PCM repository URL. Add that URL in KiCad under **Preferences → Plugins →
      Manage**, install the package from it, and confirm the action appears in the PCB editor
      toolbar and refuses correctly with `COPPER_MCP_ALLOW_LIVE_IPC` unset.
- [ ] Open the merge request against `main` of `kicad/addons/metadata`. Never open one against
      <https://gitlab.com/kicad/addons/repository> — its README says not to, and it is regenerated
      automatically.
- [ ] After merge, allow up to a day for the scheduled job to propagate the package to the public
      repository, then verify it appears in a stock KiCad's PCM.
- [ ] Record the merge request URL and the published `download_sha256` in the release ledger.

Two policy points that were checked and hold, so they do not need re-litigating each release:
the reverse-DNS namespace is tied to the GitHub account that hosts the source, and Apache-2.0 is on
the repository's accepted list and is GPL-3.0-compatible as its code-package rule requires. The
commercial-services clause does not apply; CopperMCP connects to no service.
