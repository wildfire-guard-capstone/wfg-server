# 의성 ELMFIRE 재현 사례

이 폴더는 경상북도 의성군 안평면 괴산리 산61 주변의 산불 확산 실험을 같은 조건으로 재현하고 실행하기 위한 사례입니다. ELMFIRE 원본 소스 코드는 수정하지 않았으며, 원자료 변환·입력 생성·시뮬레이션 실행·후처리 과정을 별도 스크립트로 자동화했습니다.

## 1. 무엇을 자동화했는가

처리 순서는 다음과 같습니다.

1. 원자료와 설정 파일 존재 여부 확인
2. Copernicus DEM을 EPSG:5179, 30 m 격자로 변환하고 경사·사면방향 생성
3. 의성 ASOS 시간자료를 8밴드 풍속·풍향 래스터로 변환
4. ESA WorldCover를 FBFM40 연료모델로 변환
5. 국내 임상도 ZIP을 자동으로 풀고 산림 연료모델, 수관피복률, 수고, 지하고, 수관밀도 생성
6. 기본 연료·보정계수·초기 phi와 ELMFIRE용 8밴드 landscape 파일 생성
7. 기상·토지피복·임상도·수관·수관화·비화 시나리오 실행
8. 도착시간 GeoTIFF, 1~5시간 등시선 GeoPackage, 시나리오 요약 CSV 생성

단계별 스크립트는 `scripts` 폴더에 있고 공통 좌표, 범위, 발화점, 자료 경로, 변환표는 `pipeline_config.json`에 있습니다.

## 2. 빠른 실행 방법

저장소 루트에서 워커 이미지를 준비합니다. 워커는 ELMFIRE와 GDAL 실행 환경을 포함합니다.

```bash
docker compose --profile worker build worker
```

예제는 Git으로 추적되는 코드와 로컬 실행자료를 분리합니다. 최초 한 번 예제를 `runs`로 복사합니다.

```bash
mkdir -p runs
cp -R spread/examples/uiseong_case runs/uiseong_case
```

`source_data/README.md`에 적힌 원자료를 `runs/uiseong_case/source_data`에 배치한 뒤 검증합니다.

```bash
docker compose --profile worker run --rm worker \
  bash -lc "cd /app/runs/uiseong_case && python scripts/00_validate.py"
```

원자료부터 입력만 다시 생성하려면 다음 명령을 사용합니다.

```bash
docker compose --profile worker run --rm worker \
  bash -lc "cd /app/runs/uiseong_case && python scripts/run_all.py --prepare-only"
```

특정 시나리오 실행과 후처리는 다음과 같습니다.

```bash
docker compose --profile worker run --rm worker \
  bash -lc "cd /app/runs/uiseong_case && python scripts/06_run_scenarios.py --scenario spot_test"
docker compose --profile worker run --rm worker \
  bash -lc "cd /app/runs/uiseong_case && python scripts/07_postprocess.py --scenario spot_test"
```

전체 과정은 `python scripts/run_all.py`로 실행합니다. 기존 결과가 있으면 기본적으로 해당
시나리오를 건너뜁니다. 의도적으로 다시 계산할 때만 `--force`를 붙입니다. 이 옵션은 선택한
시나리오의 결과 폴더를 비운 뒤 다시 실행합니다.

## 3. 단계별로 실행하는 방법

오류 원인을 확인하거나 일부 입력만 갱신할 때 사용합니다.

```text
00_validate.py             원자료와 시나리오 설정 확인
01_prepare_terrain.py      DEM, 경사, 사면방향
02_prepare_weather.py      풍속, 풍향, 연료수분
03_prepare_landcover.py    WorldCover 기반 연료모델
04_prepare_forest.py       임상도 기반 연료·수관 입력
05_build_landscapes.py     ELMFIRE 8밴드 landscape
06_run_scenarios.py        ELMFIRE 실행
07_postprocess.py          QGIS용 GeoTIFF·등시선·요약표
```

예를 들어 임상도 자료를 교체했다면 04, 05, 06, 07 순서로 실행하면 됩니다.

## 4. 주요 입력과 결과

- 원자료: `source_data`
- 변환된 ELMFIRE 입력: `inputs`
- 시나리오별 결과: `outputs_*`
- 전체 설정: `pipeline_config.json`
- 결과 요약: `scenario_summary.csv`

최종 landscape의 밴드 순서는 다음과 같습니다.

1. 고도
2. 경사
3. 사면방향
4. FBFM40 연료모델
5. 수관피복률
6. 수고(0.1 m 단위 정수)
7. 지하고(0.1 m 단위 정수)
8. 수관연료밀도(0.01 kg/m³ 단위 정수)

## 5. 활용 방법

### 결과만 확인하는 경우

QGIS에서 `time_of_arrival_*_fixed.tif`와 `hourly_isochrones_*_clean.gpkg`를 불러오면 됩니다. GeoTIFF는 색상으로 도착시간을, GeoPackage는 1~5시간 화선 경계를 보여 줍니다.

### 조건을 변경하여 실험하는 경우

`inputs/elmfire_*.data`를 복사하여 풍속, 수관화, 비화 설정을 바꾸고 `06_run_scenarios.py`의 시나리오 목록을 `pipeline_config.json`에 추가하면 됩니다. 원본 시나리오를 남겨 두면 결과 비교가 쉬워집니다.

### 서버 기능과 연결하는 경우

이 사례는 `spread/examples/uiseong_case`의 기준 구현입니다. 이후 `spread` 작업자가 요청값을 받아
`pipeline_config.json`과 ELMFIRE 설정을 생성하도록 연결할 수 있습니다. 서버 API에 연결하기 전
입력 생성 규칙과 실행 순서를 검증하는 회귀시험 사례로도 사용할 수 있습니다.

## 6. 재현 시 주의사항

- 기존 실험의 `ws_hourly.tif`는 ASOS m/s에 1.943844를 곱한 값으로, 정확히는 knot 변환입니다. 같은 결과를 재현하기 위해 기본 프로필을 `legacy_knots`로 두었습니다.
- ELMFIRE가 요구하는 풍속 단위에 맞춘 신규 실험은 `02_prepare_weather.py --wind-profile mph_recommended --suffix hourly_mph`로 별도 생성한 뒤 설정 파일의 `WS_FILENAME`을 바꾸어 비교해야 합니다.
- M1·M10·M100 값은 현재 실험에서 사용한 값을 그대로 설정 파일에 고정했습니다. 국내 실측 연료수분 또는 검증된 추정식을 확보하면 교체해야 합니다.
- WorldCover→FBFM40, 국내 수종→FBFM40, 지하고·수관밀도 변환은 현재 연구용 가정입니다. 관측자료로 보정되기 전에는 운영 예측값으로 단정하면 안 됩니다.
- `spot_test`는 비화 동작 확인을 위한 강한 시험 조건입니다. 실제 예측용 파라미터로 바로 사용하지 않습니다.
- `DUMP_CROWN_FIRE_AREA=.TRUE.`는 현재 사용한 ELMFIRE 1.1에서 후처리 중 오류가 발생하여 수관화 사례에서는 `.FALSE.`로 사용했습니다.

## 7. 저장소 포함 범위

공개 저장소에는 스크립트, 설정, ASOS CSV와 원자료 목록만 포함합니다. DEM·WorldCover·임상도,
생성된 GeoTIFF, `scratch`, `outputs_*`는 저장소 정책에 따라 커밋하지 않습니다. 원자료의 파일명,
크기, 출처와 SHA-256 체크섬은 `source_data/README.md`에서 확인할 수 있습니다.
