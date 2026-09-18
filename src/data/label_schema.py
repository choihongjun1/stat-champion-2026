"""인허가 3종(일반음식점·휴게음식점·미용업) 컬럼 매핑과 폐업 라벨 패널 상수.

`docs/W1_MDIS_AND_LABEL.md` §B-2 + 실제 CSV 헤더 실측 결과를 코드의 단일 출처로 고정한다.
컬럼명 위치 인덱싱은 사용하지 않는다. 3개 원본 파일은 컬럼 구성/순서가 서로 다르지만,
공통으로 존재하는 컬럼은 모두 동일한 한글 헤더 문자열을 쓰므로(실측 확인) 이름만 다른
매핑표(영문 rename)는 필요 없다 - 파일별로 존재하는 공통 컬럼만 선택하면 된다.
"""

RAW_SOURCE_TYPES = ["미용업", "일반음식점", "휴게음식점"]

# 실제 raw 헤더 열 수 (실측, DATA_CATALOG.md §1 "39/39/37열"과 일치).
# load_licensing_raw()에서 스키마 드리프트 조기 감지용 assert 대상.
EXPECTED_RAW_COLUMN_COUNTS = {"미용업": 37, "일반음식점": 39, "휴게음식점": 39}

# 3개 파일 헤더 교집합(실측 비교로 확인) - 이 컬럼만 표준화 단계에서 추출한다.
# 데이터갱신시점/최종수정시점 계열은 파일별로 컬럼명 자체가 달라(예: 일반음식점만
# "데이터갱신일자"/"최종수정일자") 정확한 교집합이 아니므로 제외했다.
CORE_COLUMNS = [
    "개방자치단체코드",
    "관리번호",
    "인허가일자",
    "영업상태명",
    "영업상태코드",
    "상세영업상태명",
    "상세영업상태코드",
    "폐업일자",
    "소재지면적",
    "소재지우편번호",
    "도로명우편번호",
    "사업장명",
    "업태구분명",
    "데이터갱신구분",
    "건물소유구분명",
    "다중이용업소여부",
    "도로명주소",
    "지번주소",
    "전화번호",
    "좌표정보(X)",
    "좌표정보(Y)",
    "남성종사자수",
    "여성종사자수",
    "위생업태명",
]

# 지번주소 내 구 이름 텍스트와 개방자치단체코드를 대조하여 실측 도출한 값이다
# (공식 코드표 아님 - 3개 파일 전체에서 각 구별 지배적인 단일 코드값임을 확인함).
DISTRICT_CODES = {"광진구": "3040000", "마포구": "3130000", "영등포구": "3180000"}

# 3구 필터 후 기대 행수 (DATA_CATALOG.md §1 실측치).
# 근본 원인 규명 완료(실측, 2026-09-18): 개방자치단체코드 필드에 소수 데이터 입력 오류가
# 있어(labels.diagnose_district_filter() 참조) 코드 기반 매칭은 이 값보다 소폭 과소
# 카운트된다(-2/-1/-5). 주소텍스트(지번주소/도로명주소) 기준 매칭이 3개 파일 전부에서
# 이 값과 정확히 일치함을 확인했다 - filter_target_districts는 주소텍스트 기준을 채택한다.
EXPECTED_DISTRICT_FILTERED_ROWS = {"일반음식점": 76453, "휴게음식점": 20484, "미용업": 13418}

# 일반음식점 파일만 CP949 완전 디코딩 불가 바이트 존재.
# 실측 검증(2026-09-18): 미용업 143,675,279 bytes / 휴게음식점 207,554,044 bytes 전량을
# codecs.getincrementaldecoder("cp949")로 strict 디코딩 스캔해 오류 0건 확인.
# 일반음식점은 byte offset 약 3,855,938에서 실제 UnicodeDecodeError(0x82) 재확인.
ENCODING_ERRORS_POLICY = {"미용업": "strict", "일반음식점": "replace", "휴게음식점": "strict"}

STORE_ID_PREFIX = "LIC_"

# 원천 식별자. mtime/size 등 동적 값은 쓰지 않는다 - 로컬 체크아웃마다 달라져 재현성이
# 깨지고 합성 데이터 테스트에서 검증 불가능하기 때문. 파일명을 그대로 원천 식별자로 쓴다.
SOURCE_SNAPSHOT = {
    "미용업": "생활_미용업.csv",
    "일반음식점": "서울시 일반음식점 인허가 정보.csv",
    "휴게음식점": "식품_휴게음식점.csv",
}

MIN_ORIGIN_QUARTER = "2021Q1"
LONG_PANEL_WINDOW_MONTHS = 12

# KM 곡선을 개업 연도(인허가일자) 코호트별로 나눠 그릴 때 쓰는 경계.
# (시작연도, 종료연도(포함), 한글 라벨, 파일명 접미사) 튜플. 각 코호트는 서로 다른
# 가게 집합이며, 코호트별로 독립적인 KaplanMeierFitter를 적합해 duration=0(개업 시점)부터
# 시작하는 곡선을 비교한다 - 단일 곡선을 구간별로 잘라 보여주는 것이 아니다.
LICENSE_YEAR_COHORTS = [
    (2021, 2022, "2021~2022년 개업", "2021_2022"),
    (2023, 2023, "2023년 개업", "2023"),
    (2024, 2024, "2024년 개업", "2024"),
    (2025, 2025, "2025년 개업", "2025"),
]

# DECISIONS.md 2026-09-13 "폐업 라벨 설계": 후보 범위 3~6개월, 확정은 W2 진입 전.
MATURITY_CUTOFF_CANDIDATE_RANGE_MONTHS = (3, 6)
# 잠정값. recommend_maturity_cutoff() 산출 결과로 재검토 후 DECISIONS.md에 확정값을
# 기록하고 이 상수를 갱신한다 - 함수 기본값으로는 쓰지 않고 스크립트에서만 명시 전달한다.
# 2026-09-18 실측: build_maturity_diagnostic_table 결과 이미 끝난 달(months_ago>=1)은
# 전부 정상 범위였고 flag된 달은 당월(부분월)뿐이었다 - 권고 컷오프 1개월을 반영.
# DECISIONS.md 후보 범위(3~6개월)와는 여전히 다르므로 W2 진입 전 팀 확정 필요.
PROVISIONAL_MATURITY_CUTOFF_MONTHS = 1

# 최종 라벨 패널(parquet/코드북 출력)에 남기는 컬럼. 원본 raw 컬럼은 여기 포함되지 않으며
# 필요한 값은 모두 파생 컬럼으로 옮겨 담는다 - mdis.extract_role_columns와 동일한 취지.
PANEL_OUTPUT_COLUMNS = [
    "store_id",
    "source_type",
    "origin",
    "origin_start",
    "origin_end",
    "event_12m",
    "age_months",
    "biz_type",
    "area",
    "has_coord",
    "feature_asof",
    "source_snapshot",
    "available_at",
    "maturity_cutoff_used_months",
]

# build_label_codebook_rows가 참조하는 "정의" 텍스트. 컬럼을 추가하면 반드시 이 dict에도
# 정의를 추가한다 (누락 시 즉시 KeyError - TODO로 비워두지 않는다).
VARIABLE_DEFINITIONS = {
    "store_id": f"식별자 = '{STORE_ID_PREFIX}' + 개방자치단체코드 + '_' + 관리번호. 3구 combined 기준 중복 0건(실측 확인, 업종 간 교차 충돌 포함).",
    "source_type": "원천 파일 구분 (미용업/일반음식점/휴게음식점).",
    "origin": "관측 기준 분기 (예: '2021Q1'). Long Panel의 행 단위 키(store_id, origin) 중 하나.",
    "origin_start": "origin 분기의 첫날. 영업 중 여부 판정 기준 시점 (인허가일자 <= origin_start AND (폐업일자 결측 OR 폐업일자 > origin_start)).",
    "origin_end": "origin 분기의 마지막날. age_months/feature_asof 등 'origin 말 기준' 계산의 기준 시점.",
    "event_12m": "origin_start 이후 12개월 이내(origin_start < 폐업일자 <= origin_start+12개월) 폐업일자 존재 시 1, 아니면 0.",
    "age_months": "파생변수. origin_end 기준 (origin_end - 인허가일자) 개월수. 음수 불가(assert) - 패널 진입 조건상 인허가일자<=origin_start<=origin_end가 이미 보장됨.",
    "biz_type": "파생변수 = source_type. 업태구분명(세부 자유텍스트)은 W2 세분화 시 원본에서 별도 사용.",
    "area": "파생변수 = 소재지면적 숫자 변환값.",
    "has_coord": "파생변수 = 좌표정보(X)/좌표정보(Y) 모두 결측 아님 여부.",
    "feature_asof": "해당 행 feature 값의 기준 시점 = origin_end.",
    "source_snapshot": "값을 계산한 원천 파일 식별자 = label_schema.SOURCE_SNAPSHOT[source_type].",
    "available_at": (
        "feature가 현실에서 이용 가능해진 시점. age_months/biz_type/area/has_coord는 "
        "origin 시점에 즉시 확인 가능하므로 feature_asof와 동일값. 주의: event_12m 자체의 "
        "신고 지연 리스크는 이 컬럼이 아니라 cohort 선정 단계의 maturity_cutoff_months로 통제한다 "
        "(행 단위 available_at을 event_12m에 별도로 부여하지 않음)."
    ),
    "maturity_cutoff_used_months": (
        "이번 실행에 실제로 적용된 성숙 컷오프(개월). 잠정값 여부는 "
        "label_schema.PROVISIONAL_MATURITY_CUTOFF_MONTHS와 비교해 확인 가능."
    ),
}
