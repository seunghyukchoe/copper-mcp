from __future__ import annotations

import unittest

from copper_mcp.models import candidate_from_dict, rank_candidates


def candidate(candidate_id: str, *, drc: int, unrouted: int, vias: int) -> dict[str, object]:
    return {
        "candidate_id": f"sha256:{candidate_id * 64}",
        "base_revision": f"sha256:{'f' * 64}",
        "status": "validated",
        "router_version": "0.1.0",
        "policy": "heuristic-v1",
        "seed": 1,
        "warnings": [],
        "metrics": {
            "hard_drc_errors": drc,
            "unrouted_connections": unrouted,
            "vias": vias,
            "wire_length_mm": 10.0,
            "runtime_seconds": 1.0,
        },
    }


class CandidateTests(unittest.TestCase):
    def test_correctness_ranks_before_via_count(self) -> None:
        clean = candidate_from_dict(candidate("a", drc=0, unrouted=0, vias=50))
        broken = candidate_from_dict(candidate("b", drc=1, unrouted=0, vias=1))
        self.assertEqual(rank_candidates([broken, clean])[0], clean)

    def test_rejects_boolean_seed(self) -> None:
        payload = candidate("a", drc=0, unrouted=0, vias=1)
        payload["seed"] = True
        with self.assertRaises(ValueError):
            candidate_from_dict(payload)

    def test_rejects_coerced_metric_strings(self) -> None:
        payload = candidate("a", drc=0, unrouted=0, vias=1)
        metrics = payload["metrics"]
        assert isinstance(metrics, dict)
        metrics["vias"] = "1"
        with self.assertRaises(ValueError):
            candidate_from_dict(payload)


if __name__ == "__main__":
    unittest.main()
