from evaluation import split


def row(cid: str, area: float, grade: str = "A") -> dict:
    return {"case_id": cid, "grade": grade, "repaired_area_ha": str(area), "duration_h": "5"}


ROWS = (
    [row(f"S{i:02d}", 10.0) for i in range(10)]
    + [row(f"M{i:02d}", 50.0) for i in range(10)]
    + [row(f"L{i:02d}", 500.0) for i in range(4)]
    + [row("TINY01", 1.0), row("BADB01", 50.0, grade="B")]
)


def test_split_is_deterministic():
    a = split.split(ROWS, set())
    b = split.split(ROWS, set())
    assert [r["case_id"] for r in a[1]] == [r["case_id"] for r in b[1]]


def test_split_sizes_and_filters():
    dev, holdout, small = split.split(ROWS, set())
    ids = {r["case_id"] for r in dev + holdout}
    assert "TINY01" not in ids and "BADB01" not in ids
    assert [r["case_id"] for r in small] == ["TINY01"]
    assert len(dev) + len(holdout) == 24
    assert sum(r["case_id"].startswith("L") for r in holdout) >= split.MIN_LARGE_HOLDOUT
    assert not {r["case_id"] for r in dev} & {r["case_id"] for r in holdout}


def test_dev_only_cases_never_go_to_holdout():
    pilot = {f"M{i:02d}" for i in range(8)} | {"L00", "L01"}
    _, holdout, _ = split.split(ROWS, pilot)
    assert not pilot & {r["case_id"] for r in holdout}
