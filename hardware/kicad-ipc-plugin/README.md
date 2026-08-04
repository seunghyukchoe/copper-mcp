# CopperMCP KiCad IPC observer

This is an official KiCad IPC Python-plugin shape for the optional live observer. It
registers one PCB-editor action and prints only a digest, version metadata, byte count,
and bounded object counts. It does not call `begin_commit`, `push_commit`, `update_items`,
`create_items`, `remove_items`, or any other mutation API.

## Development install

The plugin directory is hardware-side integration code and is intentionally not included in
the Python wheel. Copy both files into the PCB Editor plugin discovery directory configured by
your KiCad installation; `plugin.json` must be at the root of the copied plugin directory. For
example, after choosing an absolute discovery directory from KiCad's add-on documentation:

```sh
export KICAD_PCB_PLUGIN_DIR='/absolute/path/to/kicad/pcb-plugin-discovery'
mkdir -p "$KICAD_PCB_PLUGIN_DIR/org.coppermcp.live-observer"
cp plugin.json coppermcp_ipc_plugin.py \
  "$KICAD_PCB_PLUGIN_DIR/org.coppermcp.live-observer/"
```

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
KiCad than the installed binding by default. The action is an observation smoke test,
not placement or routing apply.
