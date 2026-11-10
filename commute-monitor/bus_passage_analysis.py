"""Conservative vehicle progression from historical traffic-workflow artifacts.

SHMAAS provides predicted arrival and remaining stops, not continuous GPS.
This module never writes to the source bus repository.
"""
import io
import json
import os
import statistics
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

BJT = timezone(timedelta(hours=8))
CORRIDORS = (
    {"id": "school_candidate", "label": "学校附近候选站段", "from_seq": 9,
     "from_stop": "莱阳路东陆路", "to_seq": 13, "to_stop": "博兴路兰城路"},
    {"id": "bridge_candidate", "label": "西沟港小桥附近候选站段", "from_seq": 17,
     "from_stop": "金杨路金业路", "to_seq": 21, "to_stop": "金杨路云山路"},
)


def _time(value):
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(BJT) if t.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def _position(vehicle):
    """Inferred stop index; never interpreted as GPS or measured speed."""
    values = []
    for obs in vehicle.get("observations", []):
        if obs.get("direction") != 0:
            continue
        try:
            stop = int(obs["stop_seq"])
            remaining = int(obs["remaining_stops"])
        except (TypeError, ValueError, KeyError):
            continue
        if 1 <= stop <= 100 and 0 <= remaining <= stop and not isinstance(obs.get("remaining_stops"), bool):
            values.append(stop - remaining)
    if not values or max(values) - min(values) > 2:
        return None
    return int(statistics.median_low(values))


def _bus_sample(record):
    ctx = record.get("bus_context") or {}
    t = _time(ctx.get("sample_time_cst"))
    if ctx.get("status") != "ok" or t is None:
        return None
    positions = {}
    for vehicle in ctx.get("vehicles", []):
        plate = vehicle.get("plate")
        if not isinstance(plate, str) or not plate.strip():
            continue
        p = _position(vehicle)
        if p is not None:
            positions[plate] = p
    return t, positions


def _boundary(track, threshold):
    """Bracket rather than invent a precise threshold-crossing timestamp."""
    for (t0, p0), (t1, p1) in zip(track, track[1:]):
        if p0 < threshold <= p1 and 0 < (t1 - t0).total_seconds() <= 1800:
            return t0, t1
    return None


def analyze_bus_progress(current, history):
    now = _time(current.get("timestamp_beijing"))
    base = {
        "method": "inferred_stop_progress_from_remaining_stops",
        "precision": "coarse; not GPS, measured speed or precise road-segment time",
        "corridors": list(CORRIDORS),
    }
    if now is None:
        return dict(base, status="invalid_snapshot_time")

    samples = {}
    for record in [*history, current]:
        item = _bus_sample(record)
        if item is None:
            continue
        t, positions = item
        if t.date() != now.date() or t > now or not positions:
            continue
        samples[t.isoformat()] = (t, positions)  # Skip reused source samples.
    records = sorted(samples.values(), key=lambda entry: entry[0])
    tracks = {}
    for t, positions in records:
        for plate, p in positions.items():
            tracks.setdefault(plate, []).append((t, p))

    movements = []
    passages = []
    for plate, track in tracks.items():
        for (t0, p0), (t1, p1) in zip(track, track[1:]):
            mins = (t1 - t0).total_seconds() / 60
            if not 0 < mins <= 30 or p1 < p0:
                continue
            if p0 <= 25 and p1 >= 6:
                movements.append({
                    "plate": plate,
                    "from_sample": t0.isoformat(), "to_sample": t1.isoformat(),
                    "from_inferred_stop_seq": p0, "to_inferred_stop_seq": p1,
                    "stops_advanced": p1 - p0, "elapsed_minutes": round(mins, 1),
                })
        for corr in CORRIDORS:
            enter = _boundary(track, corr["from_seq"])
            leave = _boundary(track, corr["to_seq"])
            if enter is None or leave is None or leave[1] < enter[0]:
                continue
            passages.append({
                "plate": plate, "corridor_id": corr["id"],
                "earliest_possible_minutes": max(0, round((leave[0] - enter[1]).total_seconds() / 60, 1)),
                "latest_possible_minutes": max(0, round((leave[1] - enter[0]).total_seconds() / 60, 1)),
                "entry_bracket": [enter[0].isoformat(), enter[1].isoformat()],
                "exit_bracket": [leave[0].isoformat(), leave[1].isoformat()],
                "quality": "bounded_from_sparse_stop_progress; not measured journey time",
            })

    latest_time = records[-1][0] if records else None
    near = []
    if records:
        for plate, p in records[-1][1].items():
            if 6 <= p <= 25:
                near.append({"plate": plate, "inferred_stop_seq": p})
    return dict(
        base,
        status="ok" if len(records) >= 2 else "insufficient_unique_bus_samples",
        unique_bus_sample_count=len(records),
        latest_bus_sample_time=latest_time.isoformat() if latest_time else None,
        trackable_plate_count=sum(len(track) >= 2 for track in tracks.values()),
        recent_movements=movements[-30:],
        passage_bounds=passages[-20:],
        vehicles_near_corridors=near[:30],
    )


def _api_json(url, token):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "shanghai-commute-traffic/1.0",
    })
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response)


def _artifact_record(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "shanghai-commute-traffic/1.0",
    })
    with urllib.request.urlopen(req, timeout=10) as response:
        blob = response.read(2_000_001)
    if len(blob) > 2_000_000:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [name for name in archive.namelist()
                 if name.endswith(".json") and not name.startswith("__MACOSX/")]
        if len(names) != 1 or archive.getinfo(names[0]).file_size > 1_000_000:
            return None
        return json.loads(archive.read(names[0]))


def load_prior_snapshots(now):
    """Best-effort history from artifacts in THIS repo, never from the bus repo."""
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not token or repo != "lilyQ-stack/shanghai-commute-traffic":
        return [], "not_configured"
    try:
        data = _api_json(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=40", token)
        candidates = []
        for artifact in data.get("artifacts", []):
            if artifact.get("expired") or not str(artifact.get("name", "")).startswith("baidu-traffic-"):
                continue
            created = _time(artifact.get("created_at"))
            if created is None or created.date() != now.date() or created > now:
                continue
            candidates.append(artifact)
        prior = []
        for artifact in candidates[:8]:
            try:
                record = _artifact_record(artifact["archive_download_url"], token)
                if isinstance(record, dict):
                    prior.append(record)
            except Exception:
                continue
        return prior, "ok"
    except Exception as exc:
        return [], "history_unavailable:" + type(exc).__name__


def build_analysis(current, now):
    prior, status = load_prior_snapshots(now)
    result = analyze_bus_progress(current, prior)
    result["history_lookup_status"] = status
    result["prior_traffic_snapshot_count"] = len(prior)
    if status != "ok" and result["status"] == "insufficient_unique_bus_samples":
        result["status"] = "history_unavailable"
    return result
