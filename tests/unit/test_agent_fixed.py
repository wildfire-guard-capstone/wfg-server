"""고정문(fixed.py): S1 판정 문단과 목록 앞 문단."""

from agent import fixed

S1 = {
    "id": "S1",
    "facts": {
        "now": {
            "stage": "2단계",
            "area_ha": 183.4,
            "wind_ms": 4.9,
            "facility": 23,
            "facility_is_minimum": True,
            "by_area": "2단계",
            "by_wind": "1단계",
            "by_facility": "2단계",
        },
        "p5": {"stage": "2단계", "decided_by": ["피해면적 1,293.3 ha"]},
        "change": {"prev": "1단계", "now": "2단계", "type": "up"},
        "area": {"slices": [], "first_over": [{"ha": 1000, "until": "18:20", "area_ha": 1076.9}]},
        "commander": {
            "reader": "경상북도지사",
            "now": {
                "title": "경상북도지사",
                "reasons": [{"kind": "area", "text": "피해면적 100 ha 이상 대형산불"}],
            },
            "next": {
                "title": "산림청장",
                "reasons": [{"kind": "area", "ha": 1000, "text": "피해면적 1,000 ha 이상"}],
            },
        },
    },
}
A1 = {"id": "S1-a1", "anchors": ["p.73", "p.91"]}
A3 = {"id": "S1-a3", "anchors": ["p.72", "p.114", "시행령 제21조①", "시행령 제21조②"]}
COMMON = {"reader": "경상북도지사", "incident": {"sigungu": "의성군"}}


def test_s1_elements_differ_and_up():
    p = fixed.paragraph(S1, A1, COMMON)
    assert "평균풍속 4.9 m/s는 확산대응 1단계" in p["text"] and "최소 23동은" in p["text"]
    assert p["text"].endswith("확산대응 2단계 발령을 산림청장과 협의하십시오 [p.91].")
    assert p["category"] == "협의" and p["source"] == "fixed"


def test_s1_commander_not_yet_over():
    t = fixed.paragraph(S1, A3, COMMON)["text"]
    assert t.startswith("피해면적 100 ha 이상 대형산불로 현재 지휘권자는 경상북도지사입니다.")
    assert "피해면적 1,000 ha 이상이 되면 산림청장이 통합지휘합니다" in t
    assert "18:20까지 1,076.9 ha" in t and "넘는 즉시 산림청장에게" in t


def test_upper_reader_delegates_evacuation():
    item = {"id": "E2", "facts": {"immediate": [{"emd": "안평면"}, {"emd": "안동시"}]}}
    blk = {"id": "E2-a1", "anchors": ["p.75", "p.76", "p.79"]}
    t = fixed.paragraph(item, blk, COMMON)["text"]
    assert t.startswith("의성군수·안동시장에게 아래 시각까지")
    assert "대피시키도록 지시하십시오 [p.75·76·79]." in t
    t2 = fixed.paragraph(item, blk, {"reader": "의성군수"})["text"]
    assert t2.endswith("대피시키십시오 [p.75·76·79].")


def test_missing_anchor_falls_back_to_llm():
    assert fixed.paragraph({"id": "E1"}, {"id": "E1-a1", "anchors": []}, COMMON) is None
    assert fixed.head_of("동구") == "동구청장" and fixed.head_of("의성군") == "의성군수"
