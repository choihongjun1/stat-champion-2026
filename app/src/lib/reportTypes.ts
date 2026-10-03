// 공개 리포트 계약 public-static-0.1 (스키마 0.3 기반) — src/serving/report_schema.json $defs/public_report,
// docs/REPORT_SCHEMA.md W3-14. 화면 표시 규칙은 'DASH 화면 데이터 안내서' 3절.
// 아래 타입 선언의 필드 이름은 데이터가 아니라 형식 정의다 (검사기 ID 규칙 예외: 줄 끝 claims-allow).

export type FactorId =
  | "tenure"
  | "store_profile"
  | "district"
  | "trdar_population"
  | "trdar_vitality"
  | "online_attention"
  | "peer_competition"
  | "peer_sales"
  | "rent_level";

export type MissingReason =
  | "out_of_trdar"
  | "trdar_quarter_unavailable"
  | "industry_unpublished"
  | "sales_unpublished"
  | "trdar_unknown"
  | "online_unobservable"
  | "unknown";

export type Factor = {
  factor_id: FactorId;
  name: string;
  category: "입지·수요" | "경쟁" | "비용" | "사업체 구조";
  actionability: "owner" | "policy" | "external";
  contribution: number; // 확률 단위. 0.052 = +5.2%p
  direction: "위험 증가" | "위험 감소" | "영향 미미";
  peer_percentile: number | null; // 높을수록 위험 기여가 큼
  explanation: string; // 표시 금지 — 문장 안에 %p 숫자가 있다 (ownerText로 변환)
  driver: string | null; // 온라인 요인의 주된 근거 (고정 템플릿)
  driver_code: "decline" | "lapse" | "absent" | "unobservable" | "no_change" | "presence" | null;
  interpretation_sensitive: boolean; // 비교 기준(배경)을 바꾸면 방향·표시가 달라진 요인
  sensitivity_label: string; // true면 "해석 민감", false면 ""
  values: Record<string, number | string | boolean | null>;
  display: boolean;
  data_missing: boolean;
  missing_reason: MissingReason | null;
  hold_reason: "data_missing" | "online_review" | null;
  display_note: string | null;
};

export type Prescription = {
  id: string;
  title: string;
  actionability: "owner" | "policy" | "external";
  related_factor_ids: FactorId[];
  status: "unavailable"; // W3 전까지
  unavailable_reason: string;
  evidence_level: null;
  effect_value: null;
  effect_summary: null;
  source: string | null;
  caveat: string | null;
};

export type Policy = {
  id: string;
  name: string;
  operator: string;
  link: string | null;
  eligibility_text: string;
  // #54(W3-9)에서 추가 예정 — 이름 미확정. 없을 때도 화면이 깨지지 않게 선택 필드로 둔다
  purpose?: string | null;
  apply_status?: string | null;
  apply_end?: string | null;
  announce_year: number | null;
  collected_at: string;
  match_status: "matched" | "check_required";
  matched_by: ("gu" | "biz_type" | "tenure")[];
  unverifiable_conditions: string[];
  linked_factor_ids: FactorId[]; // 표시 금지 (T4)
  check_note: string | null;
};

export type Report = {
  _schema_version: string; // "0.3"
  _public_contract_version?: string; // "public-static-0.1"
  store_id: string; // claims-allow: ID-01
  score_origin: string;
  as_of: string;
  store: {
    biz_type: "일반음식점" | "휴게음식점" | "미용업";
    gu: string;
    dong: string | null; // 법정동
    name: string | null;
    address_road: string | null; // claims-allow: ID-06
    address_jibun: string | null; // claims-allow: ID-06
    license_date: string | null;
    mdis_industry_code: string | null;
    status: { open_at_as_of: true; current: "open" | "closed" | "unknown"; close_date: string | null; license_snapshot_date: string | null };
  };
  // 공개 risk 허용 목록 (W3-14). 개인 확률·예측구간·동종 중간값은 공개 리포트에 없다.
  risk: {
    band: "low" | "mid" | "high";
    percentile: number | null; // 동종(구×업종) 안 순위, 높을수록 위험
    peer_group: string;
    model: string; // 표시 금지
    calibrated: boolean; // 표시 금지
  };
  factors: Factor[];
  unavailable_categories: string[];
  prescriptions: Prescription[];
  online_presence: null | { // 표시 금지 (T3: 점검 항목 제외)
    basis: "current_snapshot";
    collected_at: string;
    naver_local_registered: boolean | null;
    kakao_registered: boolean | null;
    naver_blog_total_12m: number | null;
    first_date_truncated: boolean | null;
    note: string;
  };
  policy_matching: "performed" | "not_performed";
  policies: Policy[];
  disclaimer: string;
};

export type SearchEntry = {
  store_id: string; // claims-allow: ID-01
  name: string | null;
  name_norm: string | null;
  biz_type: string;
  gu: string;
  dong: string | null;
  address_road: string | null; // claims-allow: ID-06
  address_jibun: string | null; // claims-allow: ID-06
};

export type BundleMeta = {
  _schema_version: string;
  run_id: string;
  score_origin: string;
  as_of: string;
  data_kind: string; // "synthetic_sample"이면 합성 배너, 그 밖은 시연(비식별) 배너
  public_contract_version?: string;
  band_cutoffs: { cut_mid: number; cut_high: number };
  policy_matching: "performed" | "not_performed";
  report_path_template: string;
  files: { search_index: string; dongs: string; dong_summary: string };
  publication_approved: boolean;
  publication_note?: string;
};

export type DongEntry = { gu: string; dong: string; label: string };

export type DongSummaryRow = {
  gu: string;
  dong: string;
  biz_type: string | null; // null = 동 전체
  n_stores: number | null;
  suppressed: boolean;
  suppression_reason: "small_cell" | "complementary" | null;
  band_share: { low: number; mid: number; high: number } | null;
  top_risk_biz_types: string[] | null;
  note: string;
};
