"""이 임시 API만 쓰는 설정(공통 설정 app.core.config와 따로 — 폴더째 지우면 같이 사라진다)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DemoSettings(BaseSettings):
    # env_ignore_empty: `RESULTS_DIR=`처럼 비워 둔 값은 기본값(None)으로 본다.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # 결과 조회 API(incidents_router) — demo.uiseong.export_bundle이 만든 폴더.
    # 제안 근거에 표준매뉴얼 원문이 들어 있으므로 저장소 밖에 둔다. 비우면 503.
    results_dir: Path | None = None
    # 로그인(auth_router) — 계정 파일(python -m app.demo_results.accounts)과 토큰 서명 키.
    # 둘 중 하나라도 비우면 로그인이 503을 돌려준다.
    accounts_file: Path | None = None
    auth_secret: str | None = None
    token_ttl_minutes: int = 480


@lru_cache
def get_demo_settings() -> DemoSettings:
    return DemoSettings()
