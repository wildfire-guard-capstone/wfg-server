# 의성 사례 원자료 준비

공개 저장소 정책에 따라 DEM·토지피복·임상도 래스터와 실행 결과는 Git으로 배포하지 않는다.
아래 자료를 직접 내려받아 이 문서에 표시된 상대 경로에 배치한다. ASOS CSV는 크기가 작고
재현에 필요한 원자료이므로 이 폴더에 함께 포함되어 있다.

## 필요한 파일

| 자료 | 저장 경로 | 크기(byte) | SHA-256 |
|---|---|---:|---|
| Copernicus DEM | `copernicus_dem_N36E128.tif` | 48,490,284 | `8AF5B10B27529358DB96D8B019C8C5FEE09426DCC48A831FE5F16514FEBD47F8` |
| ESA WorldCover 2021 | `ESA_WorldCover_10m_2021_N36E126.tif` | 87,371,384 | `85E6CFA1B0B0EB845FF6319C35B0C05CEEF0C52E935D6BE650543C75DFB85F06` |
| 의성군 임상도 | `forest_map/47730.zip` | 94,175,860 | `A6A5D27E4E6D8158C132613C0F041A2B9F6B6CECBFE0B3B3510F56AB9DEA4095` |
| 의성 ASOS | `asos_278_20250312_20250322.csv` | 11,334 | `492ED27E3DD755C1207ED9A3964F7F054A70CD8EE75B6376F16E382135273651` |

## 자료 출처

- Copernicus DEM: [Copernicus Data Space](https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM)
- ESA WorldCover: [WorldCover Data Access](https://esa-worldcover.org/en/data-access)
- 임상도: [산림공간정보서비스](https://map.forest.go.kr/forest/)
- ASOS: [기상자료개방포털](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36)

임상도 ZIP은 `04_prepare_forest.py`가 처음 실행될 때 `forest_map/extracted`에 자동으로 푼다.
파일이 다르면 아래 명령으로 SHA-256 값을 비교한다.

```powershell
Get-FileHash -Algorithm SHA256 <파일경로>
```

각 자료의 이용조건과 재배포 조건은 다운로드 시점의 제공기관 정책을 따른다.
저장소에 포함된 ASOS CSV는 원본 CP949 파일을 내용 변경 없이 UTF-8로 변환한 파일이다.
