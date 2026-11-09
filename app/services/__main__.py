"""개발 · 시험용 명령줄: 산불 만들기 → 예측 → 규칙 엔진.

    python -m app.services incident --emd 안평면 --at "2025-03-22 11:24"
    python -m app.services predict --incident 1 --predictor synthetic
    python -m app.services predict --incident 1 --predictor file --path p.geojson [--crs 5179]
    python -m app.services engine --incident 1 [--prediction 3] [--out out.json]
    python -m app.services user --login-id admin01 --role ADMIN --name 전산관리자 --org 의성군
    python -m app.services user --login-id cmd01 --role COMMANDER --name 의성군수 --org 의성군 \
        --sigungu 의성군 [--password …]   # 비밀번호를 안 주면 임시 비밀번호를 만들어 한 번 보여 줌

대상 DB는 설정(DATABASE_URL · POSTGRES_* · DB_HOST)을 따른다. 쓰기 전에 대상 DB를 출력한다.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from shapely.geometry import Point
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import temporary_password
from app.db.models.enums import UserRole
from app.db.session import SessionLocal, engine
from app.predict import PREDICTORS, get_predictor
from app.repositories import incidents
from app.repositories.engine_inputs import KST
from app.services.accounts import create_user, sigungu_id
from app.services.engine import run_engine
from app.services.prediction import run_prediction

DEV_LOGIN = "dev"


def dev_user(session: Session) -> int:
    return incidents.get_or_create_user(session, DEV_LOGIN, "개발용 계정", UserRole.ADMIN)


def emd_point(session: Session, name: str) -> Point:
    """읍면동 안의 한 점(발화점 대신). 같은 이름이 여럿이면 오류."""
    rows = session.execute(
        text(
            "SELECT ST_X(ST_PointOnSurface(geom)), ST_Y(ST_PointOnSurface(geom)) FROM admin_areas"
            " WHERE level = 'EMD' AND name = :name"
        ),
        {"name": name},
    ).all()
    if len(rows) != 1:
        raise ValueError(f"읍면동 「{name}」이 {len(rows)}곳입니다(1곳이어야 함)")
    return Point(*rows[0])


def cmd_incident(session: Session, args: argparse.Namespace) -> None:
    at = datetime.fromisoformat(args.at).replace(tzinfo=KST)
    incident_id = incidents.create_incident(
        session,
        ignition=emd_point(session, args.emd),
        address=args.address or args.emd,
        reported_at=at,
        started_at=at,
        reported_by=dev_user(session),
    )
    session.commit()
    print(f"산불 {incident_id} 만듦(발화점 = {args.emd} 안 한 점, {at:%Y-%m-%d %H:%M})")


def cmd_predict(session: Session, args: argparse.Namespace) -> None:
    options = {}
    if args.predictor == "file":
        if not args.path:
            raise SystemExit("file 예측은 --path가 필요합니다")
        options = {"path": args.path, "crs": args.crs, "weather_path": args.weather}
    prediction_id = run_prediction(
        session,
        incident_id=args.incident,
        predictor=get_predictor(args.predictor, **options),
        requested_by=dev_user(session),
    )
    session.commit()
    print(f"예측 {prediction_id} 저장(산불 {args.incident}, {args.predictor})")


def cmd_engine(session: Session, args: argparse.Namespace) -> None:
    out = run_engine(
        session, incident_id=args.incident, reader=args.reader, prediction_id=args.prediction
    )
    session.rollback()  # 아직 저장하지 않음(개발용 계정 등 읽는 중 생긴 것도 남기지 않음)
    stage, common = out["bundles"]["stage"], out["common"]
    print(
        f"{common['incident']['name']} · 현재 {stage['now']['stage']} → P5 {stage['p5']['stage']}"
    )
    print(f"지휘권자 지금 {common['commander']['now']} · 곧 {common['commander']['next']}")
    print(f"항목 {len(out['items'])}개: {', '.join(i['id'] for i in out['items'])}")
    if args.out:
        args.out.write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"저장: {args.out}")


def cmd_user(session: Session, args: argparse.Namespace) -> None:
    password = args.password or temporary_password()
    area = sigungu_id(session, args.sigungu) if args.sigungu else None
    user = create_user(
        session,
        login_id=args.login_id,
        name=args.name,
        organization=args.org,
        role=UserRole[args.role],
        admin_area_id=area,
        password=password,
    )
    session.commit()
    print(f"계정 {user.id} 만듦: {user.login_id} ({args.role})")
    if not args.password:
        print(f"임시 비밀번호(한 번만 보여 줌): {password}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("incident", help="개발용 산불 만들기(발화점 = 읍면 안 한 점)")
    p.add_argument("--emd", required=True, help="발화 읍면동 이름(예: 안평면)")
    p.add_argument("--at", required=True, help="발생 · 접수 시각, 한국 시간(예: 2025-03-22 11:24)")
    p.add_argument("--address")
    p.set_defaults(run=cmd_incident)

    p = sub.add_parser("predict", help="현재 화선에서 예측을 돌려 저장")
    p.add_argument("--incident", type=int, required=True)
    p.add_argument("--predictor", choices=list(PREDICTORS), default="synthetic")
    p.add_argument("--path", type=Path, help="file: P1 ~ P8 GeoJSON")
    p.add_argument("--crs", type=int, default=4326, help="file: 파일 좌표계(기본 4326)")
    p.add_argument("--weather", type=Path, help="file: 기상 JSON(predictions.weather 모양)")
    p.set_defaults(run=cmd_predict)

    p = sub.add_parser("engine", help="예측 + 바탕 데이터로 규칙 엔진 실행(저장 안 함)")
    p.add_argument("--incident", type=int, required=True)
    p.add_argument("--prediction", type=int, help="기본: 그 산불의 최신 예측")
    p.add_argument("--reader", default="의성군수", help="읽는 사람 직함")
    p.add_argument("--out", type=Path, help="엔진 결과 JSON을 저장할 파일")
    p.set_defaults(run=cmd_engine)

    p = sub.add_parser("user", help="계정 만들기(첫 전산 관리자 · 개발용 계정)")
    p.add_argument("--login-id", required=True)
    p.add_argument("--role", required=True, choices=[r.name for r in UserRole])
    p.add_argument("--name", required=True)
    p.add_argument("--org", required=True, help="소속")
    p.add_argument("--sigungu", help="관할 시군구 이름(전산 관리자는 생략)")
    p.add_argument("--password", help="생략하면 임시 비밀번호를 만든다")
    p.set_defaults(run=cmd_user)

    args = parser.parse_args(argv)
    print(f"대상 DB: {engine.url.render_as_string(hide_password=True)}")
    with SessionLocal() as session:
        args.run(session, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
