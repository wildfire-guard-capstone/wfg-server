"""Enum values stored as PostgreSQL ENUM types (DB 설계: 상태값이 고정되면 ENUM).

Member names are what the DB stores (e.g. ``COMMANDER``).
"""

import enum


class UserRole(enum.Enum):
    COMMANDER = "통합지휘권자"
    VIEWER = "열람자"
    REPORTER = "상황 보고자"
    ADMIN = "전산 관리자"


class IncidentStatus(enum.Enum):
    IN_PROGRESS = "진행 중"
    ENDED = "종료"


class ResponseStage(enum.Enum):
    INITIAL = "초기대응"
    STAGE1 = "확산대응 1단계"
    STAGE2 = "확산대응 2단계"


class ProposalTrigger(enum.Enum):
    PREDICTION = "예측 갱신"
    CORRECTION = "상황 정정"


class ItemAxis(enum.Enum):
    SUPPRESSION = "진화"
    EVACUATION = "대피"


class ItemCategory(enum.Enum):
    IMMEDIATE = "즉시"
    STANDBY = "대기"
    CONSULT = "협의"
    REQUEST = "요청"


class AdminLevel(enum.Enum):
    SIDO = "시도"
    SIGUNGU = "시군구"
    EMD = "읍면동"


class FacilityType(enum.Enum):
    SHELTER = "대피소"
    VULNERABLE = "취약시설"
    HOSPITAL = "응급 이송처"
    FIRE_STATION = "소방"
    POLICE = "경찰"
    HERITAGE = "국가유산"


class ResourceKind(enum.Enum):
    HELICOPTER = "헬기"
    VEHICLE = "차량"


class ResourceStatus(enum.Enum):
    AVAILABLE = "대기"
    DEPLOYED = "투입"
    MAINTENANCE = "정비"
