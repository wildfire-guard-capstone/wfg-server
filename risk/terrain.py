"""지형 변수 7개를 DEM(정사각 격자, 행 0 = 북쪽)에서 계산한다.

정의는 README 「설계 결정」 T-1 ~ T-6. 지형위치지수 · 지형복잡도 · 물길 문턱값은 AI Hub 라벨 중
의성 DEM 범위 안 질의 지점 131개(의성 의성 중리 · 군위 소보 보현 · 예천 풍양)의 값을 가장 잘
재현하는 후보로 골랐다.

- 경사 · 사면방향: Horn(1981) 3×3 — ELMFIRE 입력(11/1, step1_terrain.py)과 같은 식.
  경사 0.5° 미만은 평지
- 곡률: Zevenbergen & Thorne(1987) 전곡률 −2(D + E) × 100
  (ArcGIS 「Curvature」 단위 1/100 m, 볼록 = +)
- 지형위치지수(TPI): 고도 − 반경 150 m 원 안 평균 고도(Weiss 2001)
- 지형복잡도: 7 × 7칸(30 m 격자에서 210 m) 창 안 최고 − 최저 고도(국지 기복, m)
- 수계와의 거리: priority-flood로 웅덩이를 메운 뒤 D8 흐름 누적, 집수 면적 0.5 km² 이상 칸 = 물길
  (+ 저수지 · 호수 면 칸)까지의 거리(km)
"""

import heapq

import numpy as np

FLAT_SLOPE_DEG = 0.5  # ELMFIRE 입력 step1_terrain.py와 같은 평지 기준
TPI_RADIUS_M = 150.0
RELIEF_WINDOW = 7  # 칸
STREAM_KM2 = 0.5  # 물길로 보는 집수 면적

_NB8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _pad(z: np.ndarray) -> np.ndarray:
    return np.pad(z, 1, mode="edge")


def slope_aspect(dem: np.ndarray, cs: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(경사 °, 사면방향 °(북 = 0 시계방향, 경사면이 바라보는 쪽), 평지 여부)."""
    z = _pad(dem.astype("float64"))
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * cs)  # 동쪽으로 갈수록 +
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * cs)  # 남쪽(행 아래)으로 갈수록 +
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = (np.degrees(np.arctan2(-dzdx, dzdy)) + 360.0) % 360.0
    flat = slope < FLAT_SLOPE_DEG
    return slope, aspect, flat


def curvature(dem: np.ndarray, cs: float) -> np.ndarray:
    """전곡률(1/100 m): −2(D + E) × 100. 능선 · 볼록 = +, 골짜기 · 오목 = −."""
    z = _pad(dem.astype("float64"))
    z5 = z[1:-1, 1:-1]
    d = ((z[1:-1, :-2] + z[1:-1, 2:]) / 2 - z5) / cs**2
    e = ((z[:-2, 1:-1] + z[2:, 1:-1]) / 2 - z5) / cs**2
    return -2.0 * (d + e) * 100.0


def _disk_offsets(radius_cells: int) -> list[tuple[int, int]]:
    r = radius_cells
    return [
        (dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy * dy + dx * dx <= r * r
    ]


def tpi(dem: np.ndarray, cs: float, radius_m: float = TPI_RADIUS_M) -> np.ndarray:
    """고도 − 반경 radius_m 원(가운데 칸 포함) 안 평균 고도. 가장자리는 끝 값을 늘여 채운다."""
    r = int(round(radius_m / cs))
    z = np.pad(dem.astype("float64"), r, mode="edge")
    h, w = dem.shape
    acc = np.zeros((h, w))
    offs = _disk_offsets(r)
    for dy, dx in offs:
        acc += z[r + dy : r + dy + h, r + dx : r + dx + w]
    return dem - acc / len(offs)


def _sliding(a: np.ndarray, n: int, axis: int, fn) -> np.ndarray:
    k = n // 2
    pad = [(0, 0), (0, 0)]
    pad[axis] = (k, k)
    view = np.lib.stride_tricks.sliding_window_view(np.pad(a, pad, mode="edge"), n, axis=axis)
    return fn(view, axis=-1)


def relief(dem: np.ndarray, window: int = RELIEF_WINDOW) -> np.ndarray:
    """window × window칸 창 안 최고 − 최저 고도(m). 정사각 창이라 가로 · 세로로 나눠 구한다."""
    z = dem.astype("float64")
    hi = _sliding(_sliding(z, window, 1, np.max), window, 0, np.max)
    lo = _sliding(_sliding(z, window, 1, np.min), window, 0, np.min)
    return hi - lo


def fill_order(dem: np.ndarray, eps: float = 1e-4) -> tuple[np.ndarray, np.ndarray]:
    """Priority-flood(Barnes et al. 2014) + ε: 웅덩이 · 평지를 메워 모든 칸이 가장자리로 흐르게.

    돌려주는 것: (메운 고도, 칸 번호를 처리한 순서 = 낮은 곳부터). 가장자리 칸은 밖으로 흘러 나간다.
    """
    h, w = dem.shape
    filled = dem.astype("float64").copy()
    done = np.zeros((h, w), dtype=bool)
    pq: list[tuple[float, int, int]] = []
    for r in range(h):
        for c in (0, w - 1):
            if not done[r, c]:
                done[r, c] = True
                heapq.heappush(pq, (filled[r, c], r, c))
    for c in range(1, w - 1):
        for r in (0, h - 1):
            if not done[r, c]:
                done[r, c] = True
                heapq.heappush(pq, (filled[r, c], r, c))
    order = np.empty(h * w, dtype=np.int64)
    n = 0
    push, pop = heapq.heappush, heapq.heappop
    while pq:
        zc, r, c = pop(pq)
        order[n] = r * w + c
        n += 1
        for dr, dc in _NB8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and not done[rr, cc]:
                done[rr, cc] = True
                if filled[rr, cc] <= zc:
                    filled[rr, cc] = zc + eps
                push(pq, (filled[rr, cc], rr, cc))
    return filled, order[:n]


def flow_accumulation_km2(dem: np.ndarray, cs: float) -> np.ndarray:
    """D8(가장 가파른 낮은 이웃 하나로 흐름) 집수 면적(km², 자기 칸 포함)."""
    h, w = dem.shape
    filled, order = fill_order(dem)
    z = _pad(filled)
    best = np.zeros((h, w))
    target = np.full((h, w), -1, dtype=np.int64)
    rows, cols = np.mgrid[0:h, 0:w]
    for dr, dc in _NB8:
        drop = (filled - z[1 + dr : 1 + dr + h, 1 + dc : 1 + dc + w]) / (
            cs * (np.sqrt(2.0) if dr and dc else 1.0)
        )
        rr, cc = rows + dr, cols + dc
        ok = (drop > best) & (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
        best[ok] = drop[ok]
        target[ok] = (rr * w + cc)[ok]
    tgt = target.ravel().tolist()
    acc = [1.0] * (h * w)
    for i in order[
        ::-1
    ].tolist():  # 높은 곳부터 아래로 넘긴다(낮은 이웃은 먼저 꺼냈으므로 뒤에 처리됨)
        j = tgt[i]
        if j >= 0:
            acc[j] += acc[i]
    return np.asarray(acc).reshape(h, w) * cs * cs / 1e6


def distance_km(mask: np.ndarray, cs: float) -> np.ndarray:
    """mask가 참인 칸까지의 거리(km, 칸 중심 사이). mask가 비면 전부 NaN."""
    if not mask.any():
        return np.full(mask.shape, np.nan)
    from scipy import ndimage

    return ndimage.distance_transform_edt(~mask) * cs / 1000.0


def water_distance_km(
    dem: np.ndarray, cs: float, water: np.ndarray | None = None, stream_km2: float = STREAM_KM2
) -> tuple[np.ndarray, np.ndarray]:
    """(수계까지 거리 km, 물길 칸). 수계 = 집수 면적 stream_km2 이상 칸 ∪ water(저수지 · 호수)."""
    streams = flow_accumulation_km2(dem, cs) >= stream_km2
    mask = streams if water is None else (streams | water.astype(bool))
    return distance_km(mask, cs), streams
