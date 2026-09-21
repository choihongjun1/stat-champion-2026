# W1 Freeze

- **Freeze date**: 2026-09-22
- **main commit**: `5279604` (Merge pull request #20 from choihongjun1/fix/landprice-zero-handling)
- **Environment**: Python 3.12.5 / pandas 2.3.3 / numpy 2.5.1 / pyarrow 25.0.1 / scikit-learn 1.9.1 /
  geopandas 1.1.4 / shapely 2.1.2 / pyproj 3.8.0 / matplotlib 3.11.2 / lifelines 0.30.3
  (`requirements.txt` 고정값과 동일한 새 venv에서 검증)
- **재현 방식**: `outputs/`를 비우고 main에서 전 파이프라인을 재실행해 아래 수치를 **재계산**했다.
  기억값·과거 보고값을 옮겨 적지 않았다.

이 문서는 **현재 main의 최종 사실만** 적는다. 과거 시행착오와 PR 이력은 `DECISIONS.md`에 있다.

---

## 1. Canonical population

| 항목 | 값 |
|---|---|
| 범위 | 서울 광진구·마포구·영등포구 |
| 업종 | 일반음식점 / 휴게음식점 / 미용업 (인허가 3종) |
| 모집단 필터 | **`개방자치단체코드`** (`config.TARGET_GU_CODES`) — 주소텍스트는 QA 전용 |
| 점포 수 | **110,347** (일반음식점 76,451 / 휴게음식점 20,483 / 미용업 13,413) |
| 구별 | 마포구 42,440 / 영등포구 40,708 / 광진구 27,199 |
| `store_id` 규칙 | **`{prefix}_{관리번호}`**, prefix는 `config.BUSINESS_TYPES` (GR / SR / BT) |
| 유일성 | 110,347 고유, 중복 0 |

`gov_code`는 모집단 필터 기준, `mgmt_no`는 파일 내 유일 식별자, `store_id`는 그 둘의 조합이 아니라
**업종 prefix + `mgmt_no`** 다. 표준화·ER·공간조인·라벨 네 산출물이 모두 이 규칙을 쓴다.

## 2. Label definition

| 항목 | 규칙 |
|---|---|
| origin | 분기(2021Q1~), 판정 기준 시점은 **`origin_end`**(분기 말일) |
| risk set | `인허가일자 <= origin_end AND (폐업일자 결측 OR 폐업일자 > origin_end)` |
| event_12m | `origin_end < 폐업일자 <= origin_end + 12개월` |
| maturity | `origin_end + 12개월 <= 관측한계 − 1개월` — 컷오프 **1개월 확정** (`DECISIONS.md` 2026-09-18, 코드 상수 `label_schema.MATURITY_CUTOFF_MONTHS`) |
| feature_asof | `origin_end` (= `available_at`) |
| 주 폐업 라벨 | **인허가 행정 폐업일자** |
| 소진공 소멸 | 라벨 원천 아님 — 보조정보(`sj_status`, `sj_gap_months`)로만 사용, B-3에서 생성 |

관측 한계는 raw 최신 일자 **2026-09-10**이며, 마지막 label window는 2026-06-30에 끝난다.

## 3. ER final rules

| Tier | 규칙 | 상태 |
|---|---|---|
| Tier 1 | PNU + 정규화 상호 exact | 확정 |
| Tier 2 | 지점명 결합 exact (3개 변형) | 확정 |
| Tier 3 | PNU block 내 fuzzy, threshold **0.75** | **잠정** (sensitivity·표본 검증으로 확정 예정) |
| Tier 4 | 반경 **30m** + 상호 exact/containment + score ≥ 0.75 | 확정 |

- `crowded_pnu`(후보 ≥ 50)는 **표시만** 하고 자동 강등하지 않는다.
- 재발급 링크는 **unambiguous 1:1만** 병합한다. ambiguous 링크는 병합하지 않는다.
- `sj_entity_id` = 재발급 링크로 연결한 canonical entity.
- 202503 스냅샷 좌표는 배포본 이상으로 판정돼 `coord_suspect`로 전량 표시되며 좌표 매칭에 쓰지 않는다.
- **ER은 영업기간·존재구간 겹침을 검사하지 않는다.** 겹침을 ER hard filter로 쓸지 B-3
  QA/provenance로만 쓸지는 **미합의**다 (`W1_MDIS_AND_LABEL.md` §B-3 "B-3 겹침 조건" 표).
- **`sj_status`는 ER이 만들지 않는다.** B-3 산출물이며, 만들어져도 주 폐업 라벨을 재정의하지 않는다.

## 4. Spatial final rules

- Base assignment = **within-only**. within 미매칭 점포의 `trdar_cd`는 비운다.
- `nearest_*`(d1/d2/gap/ratio/provisional flag)는 **QA·sensitivity 전용**이며 배정에 쓰지 않는다.
- polygon 복수 후보는 임의 선택하지 않고 `spatial_ambiguous`로 보존한다(현재 0건).
- 좌표계: raw EPSG:5174 → EPSG:5179. polygon 원본은 EPSG:5181.
- **한계**: 상권 polygon은 단일 스냅샷이며 historical geometry 일관성은 미검증이다.
  과거 origin에 현재 경계를 소급 적용하는 것의 영향은 보고서에 한계로 명시한다.

## 5. Temporal availability

| 데이터 | feature_asof | available_at | 규칙 |
|---|---|---|---|
| 개별공시지가 | 1월 1일 | **4월 30일** | origin < available_at(Y)이면 해당 연도 값 사용 금지 |
| 상권분석 분기지표 | 분기 | 분기 종료 후 공표 | origin 분기 자체가 아니라 **직전 분기** 값을 쓴다 |
| 소진공 스냅샷 | 스냅샷 시점 | 동일 | union은 **ER 전용**, feature는 origin 이후 스냅샷 사용 금지 |

- 공시지가 0원은 raw에 보존하고 feature로는 `land_price_{year}_valid`(0 → NA)를 쓴다.
  `land_price_zero_flag`로 표시하며 **carry-forward는 적용하지 않는다**(W2에서 별도 결정).

## 6. Golden QA counts (main `5279604` 재계산값)

### Licensing (`outputs/standardized/licenses_3gu.parquet`)

| 지표 | 값 |
|---|---|
| rows / store_id unique | 110,347 / 110,347 (중복 0) |
| 업종 | GR 76,451 / SR 20,483 / BT 13,413 |
| PNU 성공률 | 0.9982 (결측 195) |
| parse_status | ok 110,152 / addr_missing 146 / bunji_missing 37 / dong_unmatched 10 / addr_not_seoul 2 |
| 좌표 | coord_missing 9,705 / coord_suspect 1 |
| gu_mismatch | 4 |
| 인허가일자 결측 | 0 (영업 중 28,832) |

### Labels (`outputs/labels/labels_base.parquet`)

| 지표 | 값 |
|---|---|
| panel rows | **527,934** |
| unique store_id | **44,067** |
| `(store_id, origin)` 중복 | 0 |
| origins | 18개, 2021Q1 ~ 2025Q2 |
| event_12m | **61,149** (비율 **0.1158**) |
| origin별 event 비율 | 0.1019 ~ 0.1297 (2022Q1 최저 / 2023Q3 최고) |
| 업종별 rows | 일반음식점 351,177 / 휴게음식점 99,877 / 미용업 76,880 |
| 업종별 stores | 28,930 / 8,928 / 6,209 |
| area ≥ 1000㎡ | 990행 (73개 점포), 최대 7,800㎡ |
| area 결측 | 1,199행 |
| feature_asof 범위 | 2021-03-31 ~ 2025-06-30 |
| label window 범위 | 2022-03-31 ~ 2026-06-30 |
| `close_date <= feature_asof` | **0** (필수) |
| event 정의 위반 | **0** (필수) |

### ER (`outputs/matching/license_semas_matches.parquet`)

| 지표 | 값 |
|---|---|
| rows (인허가 전체 보존) | 110,347 |
| matched | **27,430** (24.86%) / unmatched 82,917 |
| Tier 1 / 2 / 3 / 4 | 22,821 / 3,046 / 1,389 / 174 |
| ambiguous | 1,344 |
| 영업 중 매칭률 / 폐업 매칭률 | 0.7150 / 0.0836 |
| crowded_pnu | True 10,414 (False 59,027 / NA 40,906) |
| match_confidence | high 25,867 / medium 1,389 / low 174 |
| 소진공 panel | 545,490행 × 7 스냅샷, (snapshot, sj_store_id) 중복 0 |
| entity | 105,264 → canonical **100,564** |
| 재발급 링크 | 4,832 (ambiguous 132 — **병합 안 함**) |
| 202503 단독 스냅샷 기반 Tier4 매칭 | **0** (필수) |

### Spatial (`outputs/spatial/spatial_joined.parquet`)

| 지표 | 값 |
|---|---|
| rows / store_id unique | 110,347 / 110,347 |
| 유효 좌표 | 100,642 |
| within 매칭 | **82,407** (유효 좌표 기준 0.8188 / 전체 0.7468) |
| polygon 복수 후보 | 0 (`spatial_ambiguous`) |
| trdar_cd 결측 | 27,940 |
| nearest QA (within 미매칭 18,234건) | d1 median 46.11m / gap median 55.76m |
| land_price_match | True 104,050 / False 6,297 |
| land_price 결측 (raw) | 2024 6,431 / 2025 6,611 / 2026 6,722 |
| land_price_valid 결측 | 2024 6,431 / 2025 6,611 / 2026 **6,732** |
| land_price_zero_flag | 10 |

### MDIS (`outputs/mdis/`)

| 지표 | 값 |
|---|---|
| Stage A / Stage B | 5,042 / 857 (B ⊆ A, `mdis_row_id` 유일) |
| treatment | 1: 857 / 0: 4,185 |
| 서울 | 474 |
| 처치군 중 매출비율 100% | **303 (35.4%)** |
| 영업이익 0원 | **588 (11.66%)** / 적자 277 |
| tenure_months | sentinel NaN 3, > 600개월 8, 최대 741 |
| 창업형태 | 신규 4,419 / 인수 543 / 승계 80 |
| 가중치 | float64, ≤ 0 건수 0 |
| 결측률 > 30% | 전자상거래 매출비율 83.0%, 창업준비활동 4종 65.45% |
| Stage B 업종 | 47: 638 / 56: 182 / 96: 37 |
| provenance | raw sha256 `da521bd6…`, pandas 2.3.3, git `5279604399` |

## 7. Known limitations

**W2 blocker 아님 (추적 대상)**

| 항목 | 현재 처리 | 보고서 한계 명시 |
|---|---|---|
| 상권 polygon 단일 스냅샷 | 현재 geometry를 과거 origin에 적용 | 필요 |
| 202503 소진공 좌표 이상 | `coord_suspect` 77,828행 전량 표시, 좌표 매칭 배제 | 필요 |
| 2026 공시지가 0원 10건 | raw 보존 + `_valid` NA + flag | 필요 |
| MDIS 영업이익 0원 588건 | 백만원 단위 반올림 추정, 미처리 | 필요 |
| MDIS 처치군 35.4%가 순수 온라인 | 미처리 | 필요(민감도 분석 권고) |
| MDIS tenure 극단값 8건 | sentinel만 NaN 처리 | 필요 |
| MDIS 창업준비활동 65.45% 결측 | 출력에 잔존, 처리 방식 미결정 | 필요 |
| `crowded_pnu` 10,414 | 표시만, 자동 강등 없음 | 필요 |
| 소진공 entity 다대일 | ER은 보존, 라벨 모집단에서 753 entity / 1,588 점포 | B-3에서 방어 |
| Tier3 threshold 0.75 | 잠정값 | 확정 필요 |
| ER validation 입력 | `data/manual/er_validation/`로 승격(git 추적), baseline은 재생성 | — |

**미결정(결정 필요)**

- 공시지가 carry-forward 적용 여부 (W2)
- **B-3 겹침 조건** — A(ER hard filter, matched 6,105건 영향) vs B(B-3 QA/provenance, 매칭 불변).
  기준 단위도 1분기 vs 90일로 통일 안 됨. 선택지·영향은 `W1_MDIS_AND_LABEL.md` §B-3 표 참조.
- MDIS 창업준비활동 결측 처리(M5)
- MDIS 96(개인서비스업) CATE 제외 시 미용업 처방 근거 공백

## 8. W2 entry conditions

**승인된 입력 산출물**

| 산출물 | key | 유일성 |
|---|---|---|
| `outputs/labels/labels_base.parquet` | `(store_id, origin)` | 유일 |
| `outputs/standardized/licenses_3gu.parquet` | `store_id` | 유일 |
| `outputs/spatial/spatial_joined.parquet` | `store_id` | 유일 |
| `outputs/matching/license_semas_matches.parquet` | `store_id` | 유일 |
| `outputs/standardized/semas_entities.parquet` | `sj_entity_id` | entity 단위 |
| 상권분석 raw | `(상권_코드, 기준_년분기_코드)` | 분기 패널 |

**보존해야 하는 flag**: `crowded_pnu`, `coord_suspect`, `coord_missing`, `gu_mismatch`,
`parse_status`, `spatial_ambiguous`, `land_price_zero_flag`, `ambiguous`(ER), `match_tier`,
`match_confidence`, `tenure_invalid_flag`, `data_flag`, `profit_outlier`

**predictor로 쓰면 안 되는 컬럼 (leakage)**

- `close_date`, `close_date_raw`, `status_code`/`status_name`/`detail_status_*` (origin 이후 행정상태)
- `last_modified_raw`, `data_updated_raw` (origin 이후 갱신 시점)
- `entity_first_snapshot`, `entity_last_snapshot`, `first_snapshot`, `last_snapshot`,
  `n_snapshots`, `has_gap`, `id_reissued` (소진공 관측 전체 구간 = origin 이후 정보 포함)
- `land_price_{year}` 중 origin < available_at(year)인 연도
- 상권분석의 origin 분기 및 이후 분기 값
- `event_12m` 파생값 일체

flag를 predictor로 쓸지는 W2에서 별도로 결정한다(현 시점에서는 보존만 요구).

**진입 게이트**

W2 master build 전에 아래를 실행해 freeze 상태와 일치하는지 확인한다.

```bash
python -m src.data.w1_invariants
```

- `STRUCTURAL` 25건: 키 유일성·시간 정합성·join 무결성·within-only·공시지가 0 처리.
  원본이 갱신돼도 **항상 성립해야 한다**. 실패 = 버그.
- `GOLDEN` 13건: freeze 시점 실측 수치. 원본이 갱신되면 정상적으로 달라질 수 있으므로,
  실패 시 재검토 후 이 문서의 §6을 갱신한다.
- 같은 검사를 `tests/test_w1_invariants.py`가 합성 데이터로도 돌린다(산출물 없으면 skip).
