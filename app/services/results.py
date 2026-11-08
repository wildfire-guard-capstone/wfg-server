"""결과 폴더(``RESULTS_DIR``) 읽기.

DB 대신 미리 만든 JSON 묶음에서 산불 · 화선 · 예측 · 제안을 꺼낸다.

폴더는 ``python -m demo.uiseong.export_bundle``이 만든다::

    <RESULTS_DIR>/
      manifest.json                         {"format": 1, "exported_at", "source", "incidents": [1]}
      incidents/<id>/incident.json          app.schemas.results.Incident
      incidents/<id>/perimeters.json        [Perimeter]
      incidents/<id>/predictions/<n>.json   Prediction (n = 1, 2, …)
      incidents/<id>/proposals/<버전>.json   Proposal (버전 = v1.1, v2.1, …)

DB가 준비되면 같은 메서드를 가진 DB 구현으로 바꾸고 라우터는 그대로 둔다.
"""

import json
import re
import threading
from pathlib import Path
from typing import Any

BUNDLE_FORMAT = 1
VERSION_RE = re.compile(r"^v\d{1,4}\.\d{1,4}$")


class BundleError(Exception):
    pass


class FileResultsStore:
    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[Path, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        manifest = self._load(root / "manifest.json")
        if manifest is None:
            raise BundleError(f"manifest.json이 없습니다: {root}")
        if manifest.get("format") != BUNDLE_FORMAT:
            raise BundleError(f"묶음 형식 {manifest.get('format')} ≠ {BUNDLE_FORMAT}")

    def _load(self, path: Path) -> Any:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        with self._lock:
            hit = self._cache.get(path)
            if hit and hit[0] == mtime:
                return hit[1]
        data = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._cache[path] = (mtime, data)
        return data

    def _dir(self, incident_id: int) -> Path:
        return self.root / "incidents" / str(int(incident_id))

    def manifest(self) -> dict:
        return self._load(self.root / "manifest.json")

    def list_incidents(self) -> list[dict]:
        out = []
        for incident_id in self.manifest().get("incidents", []):
            inc = self.incident(incident_id)
            if inc is not None:
                out.append(inc)
        return out

    def incident(self, incident_id: int) -> dict | None:
        return self._load(self._dir(incident_id) / "incident.json")

    def perimeters(self, incident_id: int) -> list[dict] | None:
        if self.incident(incident_id) is None:
            return None
        return self._load(self._dir(incident_id) / "perimeters.json") or []

    def _all(self, incident_id: int, kind: str) -> list[dict] | None:
        if self.incident(incident_id) is None:
            return None
        folder = self._dir(incident_id) / kind
        if not folder.is_dir():
            return []
        return [self._load(p) for p in folder.glob("*.json")]

    def predictions(self, incident_id: int) -> list[dict] | None:
        rows = self._all(incident_id, "predictions")
        return None if rows is None else sorted(rows, key=lambda r: r["id"])

    def prediction(self, incident_id: int, prediction_id: int) -> dict | None:
        return self._load(self._dir(incident_id) / "predictions" / f"{int(prediction_id)}.json")

    def proposals(self, incident_id: int) -> list[dict] | None:
        rows = self._all(incident_id, "proposals")
        if rows is None:
            return None
        return sorted(rows, key=lambda r: (r["perimeter_version"], r["seq"]))

    def proposal(self, incident_id: int, version: str) -> dict | None:
        if version == "latest":
            inc = self.incident(incident_id)
            version = (inc or {}).get("latest_proposal_version") or ""
        if not VERSION_RE.match(version):
            return None
        return self._load(self._dir(incident_id) / "proposals" / f"{version}.json")
