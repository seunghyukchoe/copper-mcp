"""Pinned KiCad 10.0.5 electrical-connectivity rule profile.

Source: https://gitlab.com/kicad/code/kicad/-/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/erc/erc_settings.cpp
Keys: https://gitlab.com/kicad/code/kicad/-/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/erc/erc_item.cpp

Simulation-model and footprint/fabrication validation belong to separate authorities.
They are never represented as successful capabilities by this connectivity profile.
"""

PROFILE_ID = "kicad-project-connectivity/v1"
BACKEND_VERSION = "10.0.5"
# Present in pinned qa/data/eeschema/erc_multiple_pin_to_pin.kicad_pro; the native
# 10.0.5 settings loader reads only current keys and ignores these historical entries.
LEGACY_IGNORED_RULE_KEYS = frozenset({"conflicting_netclasses", "global_label_dangling"})
OUTSIDE_CONNECTIVITY_SCOPE = frozenset(
    {"simulation_model_issue", "footprint_link_issues", "footprint_filter"}
)
NATIVE_RULE_SEVERITIES = {
    "bus_definition_conflict": "error",
    "bus_entry_needed": "error",
    "bus_to_bus_conflict": "error",
    "bus_to_net_conflict": "error",
    "different_unit_footprint": "error",
    "different_unit_net": "error",
    "duplicate_reference": "error",
    "duplicate_sheet_names": "error",
    "endpoint_off_grid": "warning",
    "extra_units": "error",
    "field_name_whitespace": "warning",
    "footprint_filter": "ignore",
    "footprint_link_issues": "warning",
    "four_way_junction": "ignore",
    "ground_pin_not_ground": "warning",
    "hier_label_mismatch": "error",
    "isolated_pin_label": "warning",
    "label_dangling": "error",
    "label_multiple_wires": "warning",
    "lib_symbol_issues": "warning",
    "lib_symbol_mismatch": "warning",
    "missing_bidi_pin": "warning",
    "missing_input_pin": "warning",
    "missing_power_pin": "error",
    "missing_unit": "warning",
    "multiple_net_names": "warning",
    "net_not_bus_member": "warning",
    "no_connect_connected": "warning",
    "no_connect_dangling": "warning",
    "pin_not_connected": "error",
    "pin_not_driven": "error",
    "pin_to_pin": "warning",
    "power_pin_not_driven": "error",
    "same_local_global_label": "warning",
    "similar_label_and_power": "warning",
    "similar_labels": "warning",
    "similar_power": "warning",
    "simulation_model_issue": "ignore",
    "single_global_label": "ignore",
    "stacked_pin_name": "warning",
    "unannotated": "error",
    "unconnected_wire_endpoint": "warning",
    "undefined_netclass": "error",
    "unit_value_mismatch": "error",
    "unresolved_variable": "error",
    "wire_dangling": "error",
}
NATIVE_PIN_MAP = (
    (0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2),
    (0, 2, 0, 1, 0, 0, 1, 0, 2, 2, 2, 2),
    (0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1, 2),
    (0, 1, 0, 0, 0, 0, 1, 1, 2, 1, 1, 2),
    (0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2),
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2),
    (1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 2),
    (0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 2),
    (0, 2, 1, 2, 0, 0, 1, 0, 2, 2, 2, 2),
    (0, 2, 0, 1, 0, 0, 1, 0, 2, 0, 0, 2),
    (0, 2, 1, 1, 0, 0, 1, 0, 2, 0, 0, 2),
    (2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2),
)
