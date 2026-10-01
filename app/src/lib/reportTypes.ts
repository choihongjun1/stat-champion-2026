// W2-5 결과 스키마 0.2 (PR #41 docs/REPORT_SCHEMA.md, src/serving/report_schema.json) — 화면이 읽는 부분

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
  explanation: string; // 분석팀 문장 — 화면에는 쓰지 않는다 (ownerText로 변환)
  driver: string | null; // 온라인 요인의 주된 근거
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
  announce_year: number | null;
  collected_at: string;
  match_status: "matched" | "check_required";
  matched_by: ("gu" | "biz_type" | "tenure")[];
  unverifiable_conditions: string[];
  linked_factor_ids: FactorId[];
  check_note: string | null;
};

export type Report = {
  _schema_version: "0.2";
  store_id: string;
  score_origin: string;
  as_of: string;
  store: {
    biz_type: "일반음식점" | "휴게음식점" | "미용업";
    gu: string;
    dong: string | null; // 법정동
    name: string | null;
    address_road: string | null;
    address_jibun: string | null;
    license_date: string | null;
    mdis_industry_code: string | null;
    status: { open_at_as_of: true; current: "open" | "closed" | "unknown"; close_date: string | null; license_snapshot_date: string | null };
  };
  risk: {
    probability_12m: number;
    ci_low: number;
    ci_high: number;
    interval_note: string;
    band: "low" | "mid" | "high";
    percentile: number | null;
    peer_group: string;
    peer_median: number;
    model: string;
    calibrated: boolean;
  };
  factors: Factor[];
  unavailable_categories: string[];
  prescriptions: Prescription[];
  online_presence: null | {
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
  store_id: string;
  name: string | null;
  name_norm: string | null;
  biz_type: string;
  gu: string;
  dong: string | null;
  address_road: string | null;
  address_jibun: string | null;
};

export type BundleMeta = {
  _schema_version: string;
  run_id: string;
  score_origin: string;
  as_of: string;
  data_kind: string;
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
