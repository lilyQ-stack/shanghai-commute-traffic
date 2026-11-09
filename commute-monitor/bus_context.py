"""Read-only context from the existing public Pudong 35 bus sampler.

Never modifies the bus repository. A bus-data failure must not stop traffic collection.
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime

BUS_URL = (
    "https://raw.githubusercontent.com/lilyQ-stack/shanghai-bus-operations/"
    "main/data/shmaas/latest-"
    + urllib.parse.quote("浦东35路")
    + ".json"
)


def get_bus_context(now):
    context = {"source": "shanghai-bus-operations", "route": "浦东35路"}
    try:
        req = urllib.request.Request(
            BUS_URL, headers={"User-Agent": "shanghai-commute-traffic/1.0", "Cache-Control": "no-cache"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.load(response)

        sampled_at = datetime.fromisoformat(payload["sample_time_cst"])
        age_seconds = round((now - sampled_at).total_seconds())
        context["sample_time_cst"] = sampled_at.isoformat()
        context["age_seconds"] = age_seconds
        context["fresh"] = 0 <= age_seconds <= 600
        context["source_success"] = payload.get("success") is True
        if payload.get("route") != "浦东35路":
            context["status"] = "wrong_route"
        elif not context["fresh"]:
            context["status"] = "stale"
        elif not context["source_success"]:
            context["status"] = "source_failed"
        else:
            context["status"] = "ok"
            context["vehicles"] = [
                {
                    "plate": v.get("plate"),
                    "directions": v.get("directions", []),
                    "roles": v.get("roles", []),
                    "observations": [
                        {
                            "direction": p.get("direction"),
                            "stop_seq": p.get("stop_seq"),
                            "stop_name": p.get("stop_name"),
                            "arrive_time": p.get("arrive_time"),
                            "remaining_stops": p.get("remaining_stops"),
                        }
                        for p in v.get("observations", [])
                    ],
                }
                for v in payload.get("vehicles", [])
            ]
    except Exception as exc:
        context["status"] = "unavailable"
        context["error_type"] = type(exc).__name__
    return context
