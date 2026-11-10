"""Regression checks for conservative SHMAAS stop progression."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "commute-monitor"))
from bus_passage_analysis import analyze_bus_progress


def snapshot(captured, source_time, plate="TEST-BUS", positions=(), status="ok"):
    return {
        "timestamp_beijing": captured,
        "bus_context": {
            "status": status,
            "sample_time_cst": source_time,
            "vehicles": [{
                "plate": plate,
                "observations": [
                    {"direction": direction, "stop_seq": stop,
                     "remaining_stops": remaining}
                    for direction, stop, remaining in positions
                ],
            }],
        },
    }


class PassageAnalysisTests(unittest.TestCase):
    def test_repeated_source_sample_does_not_invent_progress(self):
        a = snapshot("2026-11-10T06:30:00+08:00", "2026-11-10T06:22:00+08:00",
                     positions=[(0, 13, 8), (0, 17, 12)])
        b = snapshot("2026-11-10T06:35:00+08:00", "2026-11-10T06:22:00+08:00",
                     positions=[(0, 13, 8)])
        result = analyze_bus_progress(b, [a])
        self.assertEqual(result["unique_bus_sample_count"], 1)
        self.assertEqual(result["recent_movements"], [])

    def test_track_only_direction_zero_and_report_coarse_bounds(self):
        a = snapshot("2026-11-10T06:30:00+08:00", "2026-11-10T06:22:00+08:00",
                     positions=[(0, 13, 8), (0, 17, 12), (1, 25, 0)])
        b = snapshot("2026-11-10T06:40:00+08:00", "2026-11-10T06:32:00+08:00",
                     positions=[(0, 17, 5), (0, 21, 9)])
        c = snapshot("2026-11-10T06:50:00+08:00", "2026-11-10T06:42:00+08:00",
                     positions=[(0, 21, 4), (0, 25, 8)])
        result = analyze_bus_progress(c, [a, b])
        self.assertEqual(result["unique_bus_sample_count"], 3)
        self.assertEqual(result["trackable_plate_count"], 1)
        self.assertEqual([x["stops_advanced"] for x in result["recent_movements"]], [7, 5])
        self.assertEqual(result["passage_bounds"][0]["corridor_id"], "school_candidate")
        self.assertIn("not measured journey time", result["passage_bounds"][0]["quality"])

    def test_missing_context(self):
        result = analyze_bus_progress({
            "timestamp_beijing": "2026-11-10T06:45:00+08:00",
            "bus_context": {"status": "stale"},
        }, [])
        self.assertEqual(result["status"], "insufficient_unique_bus_samples")


if __name__ == "__main__":
    unittest.main()
