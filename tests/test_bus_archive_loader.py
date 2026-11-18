"""Tests for reading the original bus repository's daily history without modifying it."""
import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "commute-monitor"))
from bus_passage_analysis import analyze_bus_progress, load_bus_day_history


def record(t, position, *, route="浦东35路"):
    return {
        "sample_time_cst": t, "date_cst": t[:10], "route": route,
        "success": True,
        "vehicles": [{
            "plate": "TEST-BUS",
            "observations": [{
                "direction": 0, "stop_seq": 25,
                "remaining_stops": 25 - position,
            }],
        }],
    }


class DailyArchiveTests(unittest.TestCase):
    def test_history_loads_distinct_source_samples_and_tracks_progress(self):
        now = datetime.fromisoformat("2026-11-18T06:45:25+08:00")
        rows = [
            record("2026-11-18T05:00:00+08:00", 1),  # outside 90-minute window
            record("2026-11-18T06:22:08+08:00", 5),
            record("2026-11-18T06:32:22+08:00", 12),
            record("2026-11-18T06:42:16+08:00", 17),
            record("2026-11-18T06:55:00+08:00", 22),  # future sample
            record("2026-11-18T06:40:00+08:00", 25, route="Other"),
        ]
        raw = "\n".join(json.dumps(x, ensure_ascii=False) for x in rows)
        raw += "\nnot json\n"
        with mock.patch(
            "bus_passage_analysis.urllib.request.urlopen",
            return_value=io.BytesIO(raw.encode("utf-8")),
        ) as opener:
            history, status = load_bus_day_history(now)
        self.assertEqual(status, "ok")
        self.assertEqual(len(history), 3)
        self.assertIn("2026-11-18-", opener.call_args.args[0].full_url)
        self.assertIn("shanghai-bus-operations", opener.call_args.args[0].full_url)
        current = {
            "timestamp_beijing": now.isoformat(),
            "bus_context": {
                "status": "ok",
                "sample_time_cst": "2026-11-18T06:42:16+08:00",
                "vehicles": rows[3]["vehicles"],
            },
        }
        result = analyze_bus_progress(current, history)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["unique_bus_sample_count"], 3)
        self.assertEqual(result["trackable_plate_count"], 1)
        self.assertEqual([m["stops_advanced"] for m in result["recent_movements"]], [7, 5])

    def test_network_failure_does_not_crash_core_collector(self):
        now = datetime.fromisoformat("2026-11-18T06:45:25+08:00")
        with mock.patch(
            "bus_passage_analysis.urllib.request.urlopen",
            side_effect=URLError("unavailable"),
        ):
            history, status = load_bus_day_history(now)
        self.assertEqual(history, [])
        self.assertEqual(status, "source_unavailable:URLError")

    def test_only_future_or_wrong_date_is_rejected(self):
        now = datetime.fromisoformat("2026-11-18T06:45:25+08:00")
        raw = json.dumps(record("2026-11-19T06:40:00+08:00", 2), ensure_ascii=False)
        with mock.patch(
            "bus_passage_analysis.urllib.request.urlopen",
            return_value=io.BytesIO(raw.encode("utf-8")),
        ):
            history, status = load_bus_day_history(now)
        self.assertEqual(history, [])
        self.assertEqual(status, "no_recent_source_samples")


if __name__ == "__main__":
    unittest.main()
