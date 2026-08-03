#!/usr/bin/env bash
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 CopperMCP Contributors

set -euo pipefail

demo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
board="$demo_dir/coppertone-buffer.kicad_pcb"

usage() {
  cat <<'EOF'
Usage: ./validate.sh [--verify | --refresh-artifacts]

  --verify              Rebuild and validate in a temporary directory without
                        changing tracked files (default).
  --refresh-artifacts   Intentionally regenerate the checked-in release snapshot.
EOF
}

case "${1:-}" in
  "" | --verify) mode=verify ;;
  --refresh-artifacts) mode=refresh ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 64
    ;;
esac

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 64
fi

if [[ -n "${KICAD_CLI:-}" ]]; then
  kicad_cli="$KICAD_CLI"
elif command -v kicad-cli >/dev/null 2>&1; then
  kicad_cli="$(command -v kicad-cli)"
elif [[ -x /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli ]]; then
  kicad_cli=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
else
  echo "error: KiCad 10 kicad-cli was not found; set KICAD_CLI" >&2
  exit 127
fi

kicad_version="$("$kicad_cli" version)"
case "$kicad_version" in
  10.*) ;;
  *)
    echo "error: CopperTone requires KiCad 10; found $kicad_version" >&2
    exit 2
    ;;
esac

verify_snapshot() {
  (
    cd "$demo_dir"
    shasum -a 256 -c validation/SHA256SUMS
  )

  validation_tmp="$(mktemp -d "${TMPDIR:-/tmp}/coppertone-verify.XXXXXX")"
  cleanup_validation_tmp() {
    /bin/rm -rf -- "$validation_tmp"
  }
  trap cleanup_validation_tmp EXIT INT TERM

  python3 "$demo_dir/generate_board.py" --output-dir "$validation_tmp"

  "$kicad_cli" pcb drc \
    --format json \
    --output "$validation_tmp/drc.json" \
    --units mm \
    --severity-all \
    --exit-code-violations \
    --refill-zones \
    --save-board \
    "$validation_tmp/coppertone-buffer.kicad_pcb"

  "$kicad_cli" pcb export stats \
    --format json \
    --units mm \
    --output "$validation_tmp/board-stats.json" \
    "$validation_tmp/coppertone-buffer.kicad_pcb"

  for artifact in \
    coppertone-buffer.kicad_pcb \
    coppertone-buffer.kicad_pro \
    metrics.json; do
    if ! cmp -s "$demo_dir/$artifact" "$validation_tmp/$artifact"; then
      echo "error: regenerated $artifact does not match the checked-in snapshot" >&2
      exit 1
    fi
  done

  python3 - \
    "$demo_dir/validation/drc.json" \
    "$validation_tmp/drc.json" \
    "$demo_dir/validation/board-stats.json" \
    "$validation_tmp/board-stats.json" <<'PY'
import json
from pathlib import Path
import sys


def load_without_date(raw_path: str) -> dict[str, object]:
    data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    data.pop("date", None)
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("date", None)
    return data


expected_drc, actual_drc, expected_stats, actual_stats = map(
    load_without_date, sys.argv[1:]
)
if actual_drc != expected_drc:
    raise SystemExit("error: regenerated DRC evidence differs from the snapshot")
if actual_stats != expected_stats:
    raise SystemExit("error: regenerated board statistics differ from the snapshot")
if actual_drc.get("violations") or actual_drc.get("unconnected_items"):
    raise SystemExit("error: DRC evidence is not clean")
PY

  cleanup_validation_tmp
  trap - EXIT INT TERM
  printf 'CopperTone snapshot verified read-only with KiCad %s\n' \
    "$kicad_version"
}

refresh_artifacts() {
  # Recreate the generated trees from empty. "mkdir -p" alone leaves files from a previous
  # export in place, and anything stale would be hashed into SHA256SUMS as if this run had
  # produced it.
  rm -rf \
    "$demo_dir/validation" \
    "$demo_dir/manufacturing/gerbers" \
    "$demo_dir/manufacturing/drill" \
    "$demo_dir/mechanical" \
    "$demo_dir/media"
  mkdir -p \
    "$demo_dir/validation" \
    "$demo_dir/manufacturing/gerbers" \
    "$demo_dir/manufacturing/drill" \
    "$demo_dir/mechanical" \
    "$demo_dir/media"

  python3 "$demo_dir/generate_board.py"

  "$kicad_cli" pcb drc \
    --format json \
    --output "$demo_dir/validation/drc.json" \
    --units mm \
    --severity-all \
    --exit-code-violations \
    --refill-zones \
    --save-board \
    "$board"

  "$kicad_cli" pcb export stats \
    --format json \
    --units mm \
    --output "$demo_dir/validation/board-stats.json" \
    "$board"

  "$kicad_cli" pcb export gerbers \
    --output "$demo_dir/manufacturing/gerbers" \
    --layers F.Cu,B.Cu,F.Mask,B.Mask,F.SilkS,B.SilkS,Edge.Cuts \
    --check-zones \
    "$board"

  "$kicad_cli" pcb export drill \
    --output "$demo_dir/manufacturing/drill" \
    --format excellon \
    --excellon-units mm \
    --excellon-separate-th \
    --generate-map \
    --map-format svg \
    --generate-report \
    --report-path "$demo_dir/manufacturing/drill/drill-report.txt" \
    "$board"

  "$kicad_cli" pcb export svg \
    --output "$demo_dir/media/coppertone-buffer-copper.svg" \
    --layers F.Cu,F.SilkS,Edge.Cuts \
    --mode-single \
    --page-size-mode 2 \
    --check-zones \
    "$board"

  "$kicad_cli" pcb render \
    --output "$demo_dir/media/coppertone-buffer-top.png" \
    --width 1800 \
    --height 1100 \
    --side top \
    --background opaque \
    --quality high \
    --floor \
    --perspective \
    --rotate 342,0,25 \
    "$board"

  "$kicad_cli" pcb render \
    --output "$demo_dir/media/coppertone-buffer-bottom.png" \
    --width 1600 \
    --height 900 \
    --side bottom \
    --background opaque \
    --quality high \
    "$board"

  "$kicad_cli" pcb export step \
    --output "$demo_dir/mechanical/coppertone-buffer.step" \
    --force \
    --include-tracks \
    --include-pads \
    --include-zones \
    --include-silkscreen \
    --include-soldermask \
    "$board"

  # KiCad's SVG and STEP exporters emit harmless trailing spaces. Normalize
  # them before hashing so the checked-in text remains clean and diffable.
  python3 - \
    "$demo_dir/manufacturing/drill/coppertone-buffer-NPTH-drl_map.svg" \
    "$demo_dir/manufacturing/drill/coppertone-buffer-PTH-drl_map.svg" \
    "$demo_dir/media/coppertone-buffer-copper.svg" \
    "$demo_dir/mechanical/coppertone-buffer.step" <<'PY'
from pathlib import Path
import sys

for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(line.rstrip(" \t") for line in lines) + "\n",
        encoding="utf-8",
    )
PY

  (
    cd "$demo_dir"
    {
      printf '%s\n' \
        coppertone-buffer.kicad_pcb \
        coppertone-buffer.kicad_pro \
        metrics.json \
        validation/board-stats.json \
        validation/drc.json
      LC_ALL=C find manufacturing mechanical media -type f -print
    } | LC_ALL=C sort \
      | xargs shasum -a 256
  ) > "$demo_dir/validation/SHA256SUMS"

  printf 'CopperTone release snapshot refreshed with KiCad %s\n' \
    "$kicad_version"
}

if [[ "$mode" == verify ]]; then
  verify_snapshot
else
  refresh_artifacts
fi
