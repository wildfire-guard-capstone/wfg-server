"""기상청 API를 실제 키로 불러 보는 확인 명령(B 방식 · stations). DB · ELMFIRE 없이 돈다.

  uv run python -m app.weather.check_stations            # .env의 KMA_API_KEY · DATA_GO_KR_API_KEY
  uv run python -m app.weather.check_stations --deck ~/wfg/demo/deck   # 600 m 격자까지

  1. AWS 매분자료(API 허브): 관측소 하나(기본 278 의성)의 응답 머리말 · 앞 줄 · 시간별 요약
  2. 단기예보(공공데이터포털): 그 관측소 자리 9시간
  3. (--deck) 덱 범위 관측소 전체로 격자를 만들어 시간별 풍속 · 풍향 범위와 관측소별 돌풍 비율

키는 화면에 찍지 않는다(응답 글에도 키는 없다).
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.weather.aws_minute import URL, AwsMinute, hourly, parse_minutes
from app.weather.base import KST
from app.weather.field import kma_forecast, load_stations, station_field


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="기상청 API 확인(B 방식)")
    p.add_argument("--stn", default="278", help="관측소 번호(기본 278 의성)")
    p.add_argument("--deck", type=Path, help="덱 폴더 — 주면 600 m 격자까지 만든다")
    p.add_argument("--at", help="기준 시각(ISO, 기본 지금)")
    a = p.parse_args(argv)
    s = get_settings()
    if not s.kma_api_key or not s.data_go_kr_api_key:
        raise SystemExit("KMA_API_KEY · DATA_GO_KR_API_KEY가 .env에 있어야 합니다")
    now = datetime.fromisoformat(a.at) if a.at else datetime.now(KST)
    now = now if now.tzinfo else now.replace(tzinfo=KST)
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    print(f"== 1. AWS 매분자료 stn={a.stn} {start:%m-%d %H:%M} ~ {now:%H:%M}")
    aws = AwsMinute(s.kma_api_key)
    url = (
        f"{URL}?tm1={start:%Y%m%d%H%M}&tm2={now:%Y%m%d%H%M}&stn={a.stn}&disp=1&help=1"
        f"&authKey={s.kma_api_key}"
    )
    text = aws.get_text(url)
    lines = text.splitlines()
    print("머리말:")
    for line in [x for x in lines if x.startswith("#")][:12]:
        print("  ", line[:160])
    print("자료 앞 3줄:")
    for line in [x for x in lines if x[:1].isdigit()][:3]:
        print("  ", line[:160])
    rows = [r for r in parse_minutes(text) if r["stn"] == a.stn]
    print(f"읽은 분 자료 {len(rows)}개")
    for t, h in hourly(rows).items():
        print(f"  {t:%H}시 {h}")

    site = next((ll for st, ll in load_stations() if st.id == a.stn), (36.3561, 128.6886))
    print(f"\n== 2. 단기예보 {site}")
    for t, h in kma_forecast(s.data_go_kr_api_key)(site, now).items():
        print(f"  {t:%m-%d %H}시 {h}")

    if a.deck:
        from app.weather.elmfire_input import ElmfireWeatherRequest, deck_weather_profile

        prof = deck_weather_profile(a.deck)
        print(f"\n== 3. 격자 {prof['width']}×{prof['height']} (덱 {a.deck}) · 발화점 칸 {site}")
        inp = station_field(s.kma_api_key, s.data_go_kr_api_key).build(
            ElmfireWeatherRequest(now, site, prof)
        )
        grids, meta = inp.grids, inp.meta
        print(f"관측소 {len(meta['stations'])}곳 받는 데 {meta['fetch_s']}초(동시 호출)")
        print("쓴 관측소", meta["stations"])
        print("뺀 관측소", meta["skipped"])
        print("시간 종류", meta["kinds"], "· 예보 격자", meta["forecast_grids"])
        print("돌풍 비율", meta["gust_factor"])
        for h in range(9):
            w = grids["ws"][h] / 2.23694
            print(
                f"  +{h}시간 풍속 {w.min():.1f} ~ {w.max():.1f} m/s · 풍향 "
                f"{grids['wd'][h].min():.0f} ~ {grids['wd'][h].max():.0f}° · "
                f"m1 {grids['m1'][h].mean():.1f} %"
            )
        print("발화점 칸 9시간(저장 · 판정용):")
        for h in inp.weather["hours"]:
            print(
                f"  {h['at'][11:16]} {h['kind']} 풍향 {h['wind_dir']:.0f}° · 평균 "
                f"{h['wind_speed']:.1f} · 1분 최대 {h['wind_max_1min']:.1f} m/s · "
                f"{h['temp']:.1f} ℃ · {h['humidity']:.0f} %"
            )


if __name__ == "__main__":
    main()
