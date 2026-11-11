from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = "postgresql+psycopg://wfg:wfg@localhost:5432/wfg"  # docker-compose.yml db


class Settings(BaseSettings):
    # env_ignore_empty: `KMA_API_KEY=`처럼 비워 둔 값은 기본값(None)으로 본다.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # DATABASE_URL wins. Without it the URL is built from the POSTGRES_* values the DB
    # container itself uses (EC2 db.env), so the password lives in one file only.
    database_url: str = ""
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_db: str | None = None
    db_host: str = "localhost"
    db_port: int = 5432

    aihub_root: Path | None = None
    data_root: Path = Path("./data")
    # capston-data 「data/지도자원/적재」 — 바탕 데이터 적재 파일(app.db.seed.base)
    base_data_dir: Path | None = None
    elmfire_bin: str = "elmfire_1.1"
    kma_api_key: str | None = None  # 기상청 API 허브(지상관측 asos)
    data_go_kr_api_key: str | None = None  # 공공데이터포털(기상청 단기예보 kma)

    # 예측에 쓰는 기상 원천(app.weather): fixed · file(WEATHER_PATH) · kma · asos(WEATHER_STATION)
    # 아직 예측 흐름에 잇지 않음(4-2) — 지금은 예측 제공자가 만든 기상을 저장한다
    weather: str = "fixed"
    weather_path: str | None = None
    weather_station: str = "278"  # 지상관측 지점(278 = 의성)

    # 개발 중 다른 출처(예: Vite http://127.0.0.1:5173)에서 부를 때만. 같은 출처 배포면 비워 둔다.
    cors_origins: list[str] = []

    # 예측 API(PRED-01)가 쓰는 예측 제공자(app.predict). file이면 PREDICTOR_PATH(P1~P8 GeoJSON)
    predictor: str = "synthetic"
    predictor_path: str | None = None
    predictor_crs: int = 4326
    predictor_weather: str | None = None
    # elmfire면 PREDICTOR_DECK(덱 폴더, demo/uiseong/deck 모양) · ELMFIRE_BIN · PREDICTOR_WORK_DIR
    predictor_deck: str | None = None
    predictor_work_dir: str | None = None
    # elmfire 기상 출처(app.weather.elmfire_input):
    #   stations = 실시간(기본) — 관측소 매분자료 + 단기예보, KMA_API_KEY · DATA_GO_KR_API_KEY 필요
    #   deck = 시연 — 덱에 든 의성 관측 기상, WEATHER=asos(기온 · 습도)와 같이 쓴다
    predictor_weather_field: str = "stations"
    # 구조 B(EC2): ELMFIRE는 worker 컨테이너에서(app.predict.worker_server).
    # 주소가 있으면 API는 기상 입력만 만들어 보내고, 없으면 같은 프로세스에서 돌린다(GPU 서버).
    # 시간 제한 = 앞 실행을 기다리는 시간 포함
    elmfire_worker_url: str | None = None
    elmfire_worker_timeout_s: float = 900
    # 예측 API(PRED-01)가 쓰는 제안 작성기(app.propose): fake(테스트 문장, 기본) · agent(EXAONE +
    # 근거 검색). agent는 아래 LLM_URL · EVIDENCE_URL(GPU 서버 터널 주소)이 필요하다
    proposal_writer: str = "fake"
    proposal_llm_timeout_s: float = 600  # 제안 한 항목 호출(어시스턴트 답보다 길다)
    # 위험도(RISK-01) 지형 7 · 연료 6 변수의 지역 격자 폴더(risk.grid: grades.tif · values.tif ·
    # meta.json, demo/uiseong/build_risk_grid.py로 만듦). 요청마다 P5 근처 칸만 읽는다.
    # 비우거나 파일이 없으면 그 13개는 빼고 나머지 변수로 계산한다. EC2: /data/risk/uiseong
    risk_grid_dir: Path | None = None

    # AI 어시스턴트(AI-01): rules = LLM 없이 규칙만(개발 · 시험), exaone = vLLM(EXAONE) + 근거 서버.
    # GPU 서버의 vLLM · 근거 서버는 127.0.0.1에서만 열리므로 API 서버에서는 터널 주소를 넣는다.
    assistant: str = "rules"
    llm_url: str = "http://127.0.0.1:8031"
    llm_model: str = "exaone-4.5-33b-awq"
    llm_timeout_s: float = 120
    # 어시스턴트는 비우면 검색 없이 제안서가 인용한 원문만 쓴다. 제안 작성기 agent에는 꼭 필요하다
    evidence_url: str | None = None
    correction_ttl_s: int = 600  # 정정 확인안 유효 시간(명세 CorrectionPreview, 10분)

    # 로그인 토큰(JWT HS256). 비워 두면 서버를 켤 때마다 임시 키를 만든다(재시작하면 다시 로그인)
    jwt_secret: str = ""
    jwt_expires_s: int = 1800

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if not self.database_url:
            parts = (self.postgres_user, self.postgres_password, self.postgres_db)
            if all(parts):
                user, password, db = (quote(p, safe="") for p in parts)
                self.database_url = (
                    f"postgresql+psycopg://{user}:{password}@{self.db_host}:{self.db_port}/{db}"
                )
            else:
                self.database_url = LOCAL_DATABASE_URL
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
