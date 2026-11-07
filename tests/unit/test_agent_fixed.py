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


def test_s3_templates():
    item = {
        "id": "S3",
        "facts": {
            "deployed": {
                "helicopter": {
                    "total": 23,
                    "by": [{"label": "산림청", "n": 5}, {"label": "군", "n": 0}],
                },
                "crew": {"total": 151, "by": [{"label": "진화대", "n": 81}]},
                "vehicle": {"total": None},
            },
            "scope": {
                "now": {
                    "stage": "1단계",
                    "ground": {
                        "home": {"name": "의성군", "share": "100%"},
                        "extra": {
                            "kind": "neighbors",
                            "names": ["안동시", "청송군"],
                            "share": "50% 이내",
                        },
                    },
                    "air": {"kind": "radius", "km": 100},
                },
                "p5": {
                    "stage": "2단계",
                    "ground": {
                        "home": {"name": "의성군", "share": "100%"},
                        "extra": {"kind": "sido", "names": ["경상북도"], "share": "100%"},
                    },
                    "air": {"kind": "national", "km": None},
                },
            },
        },
    }
    a1 = fixed.paragraph(item, {"id": "S3-a1", "anchors": ["p.77", "p.78"]}, COMMON)["text"]
    assert a1.startswith("현재 투입된 자원은 헬기 23대(산림청 5), 인력 151명(진화대 81)입니다.")
    a2 = fixed.paragraph(item, {"id": "S3-a2", "anchors": ["고시 별표"], "see": "S6"}, COMMON)[
        "text"
    ]
    assert "지상이 의성군 100%와 인접 시군(안동시·청송군) 50% 이내, 공중이 발화지 반경 100 km" in a2
    assert a2.endswith("세부 내용은 S6 참조.")
    a3 = fixed.paragraph(item, {"id": "S3-a3", "anchors": ["고시 별표"]}, COMMON)["text"]
    assert "공중은 전국으로 넓어지므로" in a3


def test_s4_and_e4_road():
    s4 = {
        "id": "S4",
        "facts": {
            "sun": {"sunset": "18:38", "sunset_zone": "p5", "now_is_night": False},
            "max": {"ms": 6.9, "dir": "서", "label": "15:20"},
        },
    }
    a1 = fixed.paragraph(s4, {"id": "S4-a1", "anchors": ["p.77", "p.78"]}, COMMON)["text"]
    assert "일몰(18:38) 뒤" in a1 and a1.endswith("[p.77·78].")
    a2 = fixed.paragraph(s4, {"id": "S4-a2", "anchors": ["p.78"]}, COMMON)["text"]
    assert a2.startswith("15:20에 서풍 6.9 m/s로 바람이 가장 셉니다.")
    e4 = {
        "id": "E4",
        "facts": {
            "roads": [
                {
                    "road": "안평의성로",
                    "section": "신월리",
                    "zone": "p5",
                    "from": "15:20",
                    "police": {"name": "의성경찰서"},
                },
                {
                    "road": "이름 없는 도로",
                    "section": "철파리",
                    "zone": "p5",
                    "from": "18:20",
                    "police": {"name": "의성경찰서"},
                },
            ]
        },
    }
    blk = {"id": "E4-a3", "anchors": ["p.79", "p.95"], "see": "E6"}
    t = fixed.paragraph(e4, blk, COMMON)["text"]
    assert t.startswith(
        "안평의성로 신월리 구간, 철파리 구간 도로에 15:20부터 불이 닿을 수 있습니다."
    )
    assert "의성경찰서와 도로 관리기관에" in t and t.endswith("세부 내용은 E6 참조.")


def test_e3_check_and_transfer():
    e3 = {
        "id": "E3",
        "facts": {"daytime_only_count": 2, "weekday": "토요일", "now_is_night": False},
    }
    a2 = fixed.paragraph(e3, {"id": "E3-a2", "anchors": [], "kind": "check"}, COMMON)["text"]
    assert a2 == "주야간보호 2곳은 토요일 운영 여부와 이용자 귀가 여부를 확인하십시오."
    a4 = fixed.paragraph(e3, {"id": "E3-a4", "anchors": ["p.95"], "see": "E6"}, COMMON)["text"]
    assert a4 == "거주 시설의 거주자 이송을 요청하십시오 [p.95]. 세부 내용은 E6 참조."
