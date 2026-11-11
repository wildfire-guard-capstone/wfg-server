"""연료 변수 6개(수분함량 제외)를 ELMFIRE 입력의 연료 모델 번호(fbfm) · 수관 래스터에서 만든다.

연료 값은 확산 예측에 쓴 ELMFIRE 입력(11/1, 팀)을 그대로 쓴다(README F-1 ~ F-6):
- `inputs/fuel_model_mapping.csv`  fbfm ↔ 국내 연료 모델(수종 · 영급 · 경급,
  국립산림과학원 연구자료 제1036호 표 5 ~ 7). h1 · h10 · h100 · 낙엽층은 덱 단계 2의
  원문 오류 보정이 반영된 값
- `scripts/fuel_table_raw.csv`     같은 표 원문(연소가능 수관연료량 · 유기물층, t/ha)
- `inputs/fuel_models.csv`         ELMFIRE 연료 사전(GR1 · GR2 · GS2 연료량 lb/ft², 연료층 깊이 ft)
- `inputs/cc.tif` · `ch.tif`       수관피복률(%: 임상도 밀도 소 30 · 중 55 · 밀 85) ·
  수관높이(m × 10)

11/10 A′ 덱은 연료층 깊이 · 표면적비만 바꾸고 모델 번호 · 이름 · 연료량은 11/1과 같다. A′ 릴리스에는
대응표 두 개가 없어 `tables`로 11/1 덱의 표를 쓴다(load_fuel_models).
"""

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LB_FT2_PER_T_HA = 0.020482  # 덱 README: t/ha × 0.020482 = lb/ft²
CM_PER_FT = 30.48
CONIFER = {"소나무", "잣나무", "리기다소나무", "곰솔", "기타침엽수"}
BROADLEAF = {"상수리나무", "굴참나무", "신갈나무", "기타활엽수"}
# 표준 연료 모델(덱 단계 3의 토지피복 → 연료): GR1 · GR2 = 초지, GS2 = 관목덤불
STANDARD_TYPE = {101: "초지", 102: "초지", 122: "관목림"}
NON_BURNABLE = {91, 92, 93, 98, 99}  # 시가지 · 도로 / 논밭 / 물 / 나지 · 자료 없음


@dataclass(frozen=True)
class FuelModel:
    code: int
    type: str  # 활엽수림 · 혼효림 · 관목림 · 침엽수림 · 초지 · 비연소
    load_t_ha: float  # 연소가능 수관연료량 + 지표 연료(1 · 10 · 100시간 + 낙엽층 + 유기물층)
    depth_cm: float  # 지표 연료층 깊이(연료 사전)
    age_years: float  # 영급 n → 10n − 5년(영급 가운데). 숲이 아니면 0
    species: str = ""


def _csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _forest_type(species: str) -> str:
    if species in CONIFER:
        return "침엽수림"
    if species in BROADLEAF:
        return "활엽수림"
    if species == "혼효림":
        return "혼효림"
    raise ValueError(f"모르는 수종: {species}")


def load_fuel_models(deck: Path, tables: Path | None = None) -> dict[int, FuelModel]:
    """덱 폴더(elmfire_deck) → 연료 모델 번호별 위험도 연료 값.

    tables: 대응표(inputs/fuel_model_mapping.csv · scripts/fuel_table_raw.csv)를 읽을 덱,
    비우면 deck. 11/9 · 11/10 최종 덱 묶음에는 이 두 표가 없다 — 연료 모델 번호가 11/1 덱과
    같으므로 11/1 덱(<input>/elmfire_deck)의 표를 쓴다. 연료층 깊이 · GR · GS 연료량은 늘 deck의
    fuel_models.csv.
    """
    deck = Path(deck)
    tables = Path(tables) if tables else deck
    lib = {}
    for row in csv.reader(open(deck / "inputs" / "fuel_models.csv", encoding="utf-8-sig")):
        if row and row[0].strip().isdigit():
            code = int(row[0])
            loads = [float(x) for x in row[3:8]]  # 1h · 10h · 100h · 생초본 · 생목본 (lb/ft²)
            lib[code] = (sum(loads) / LB_FT2_PER_T_HA, float(row[11]) * CM_PER_FT)
    raw = {
        (r["수종"], float(r["영급"]), float(r["경급"])): r
        for r in _csv(tables / "scripts" / "fuel_table_raw.csv")
    }
    out: dict[int, FuelModel] = {}
    for r in _csv(tables / "inputs" / "fuel_model_mapping.csv"):
        code = int(r["fbfm"])
        sp, age, dia = r["수종"], float(r["영급"]), float(r["경급"])
        src = raw[(sp, age, dia)]
        surface = sum(float(r[k]) for k in ("h1", "h10", "h100", "낙엽층"))
        load = float(src["연소가능수관연료량"]) + surface + float(src["유기물층"])
        out[code] = FuelModel(
            code, _forest_type(sp), round(load, 3), round(lib[code][1], 2), 10 * age - 5, sp
        )
    for code, t in STANDARD_TYPE.items():
        if code in lib:
            out[code] = FuelModel(code, t, round(lib[code][0], 3), round(lib[code][1], 2), 0.0)
    for code in NON_BURNABLE:
        out[code] = FuelModel(code, "비연소", 0.0, 0.0, 0.0)
    return out


TYPE_ID = {"비연소": 0, "활엽수림": 1, "혼효림": 2, "관목림": 3, "침엽수림": 4, "초지": 5}
TYPE_NAME = {v: k for k, v in TYPE_ID.items()}


def fuel_layers(fbfm: np.ndarray, models: dict[int, FuelModel]) -> dict[str, np.ndarray]:
    """연료 모델 번호 격자 → fuel_type(TYPE_ID) · fuel_load · fuel_depth · stand_age.

    사전에 없는 번호는 NaN(값 없음).
    """
    codes = np.unique(fbfm)
    keys = ("fuel_type", "fuel_load", "fuel_depth", "stand_age")
    out = {k: np.full(fbfm.shape, np.nan) for k in keys}
    for c in codes.tolist():
        m = models.get(int(c))
        if m is None:
            continue
        sel = fbfm == c
        out["fuel_type"][sel] = TYPE_ID[m.type]
        out["fuel_load"][sel] = m.load_t_ha
        out["fuel_depth"][sel] = m.depth_cm
        out["stand_age"][sel] = m.age_years
    return out


def fuel_type_grade(type_id: np.ndarray) -> np.ndarray:
    """TYPE_ID → 수종종류 등급(criteria.FUEL_TYPE_GRADE, 비연소 = 1)."""
    from risk.criteria import FUEL_TYPE_GRADE

    out = np.full(type_id.shape, np.nan)
    for tid, name in TYPE_NAME.items():
        out[type_id == tid] = FUEL_TYPE_GRADE[name]
    return out


def canopy_height_grade(height_m: np.ndarray, cover_pct: np.ndarray) -> np.ndarray:
    """수관높이 등급. 수관이 없는 칸(피복률 0 또는 높이 0)은 1 — 수관화로 번질 숲이 없다(F-5).

    CSV는 「낮을수록 위험(≤6 m = 5)」이라 그대로 읽으면 풀밭 · 논밭이 5등급이 된다.
    """
    from risk.criteria import grade

    g = grade("canopy_height", height_m)
    none = (np.nan_to_num(cover_pct) <= 0) | (np.nan_to_num(height_m) <= 0)
    g[none & ~np.isnan(height_m)] = 1.0
    return g
