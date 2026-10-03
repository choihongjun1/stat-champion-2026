// 분석용 위험요인 → 사장님 언어 변환 (프론트에서 변환하기로 결정, 2026-09-25)
// 규칙 (#48 검토 의견 M2·M3·M4, docs/CLAIMS.md 5절):
//  - 숫자(%p)는 보여주지 않는다. contribution은 정렬과 크기 단계(크게·조금·약간)에만 쓴다.
//  - 사실(영업 기간, 블로그 언급 수 등)을 쓰고, 끝에 "모형은 이 점을 위험을 높이는/낮추는 신호로 봤어요"를 붙인다.
//    집단의 폐업 빈도를 비교하는 문장은 쓰지 않는다 (그런 집계를 하지 않았다).
//  - 온라인 지표는 "블로그 언급"이다 (글 내용을 분석하지 않았다).
// 화면 분기는 코드 필드(display·hold_reason·missing_reason·unavailable_categories)로만 한다 (REPORT_SCHEMA §4).

import type { Factor, FactorId, MissingReason, Report } from "./reportTypes";

export type Level = "strong" | "some" | "slight" | "none";

export type OwnerFactor = {
  factorId: FactorId;
  label: string; // 화면 이름
  direction: "up" | "down";
  level: Level;
  levelText: string; // '위험을 크게 높이는 쪽이에요'
  sentence: string;
  peerText: string | null;
  sensitive: boolean; // 해석 민감
};

export type OwnerFactorView = {
  up: OwnerFactor[]; // 위험을 높인 요인 (기여 큰 순)
  down: OwnerFactor[]; // 위험을 낮춘 요인
  missingNotes: string[]; // 데이터 없음 — 사유별 한 문장
  unavailableNote: string | null; // 판단 불가 유형
  noStandout: boolean; // 가장 큰 위험 상승이 '약간' 이하
};

export const LABEL: Record<FactorId, string> = {
  tenure: "영업 기간",
  store_profile: "업종과 가게 크기",
  district: "가게가 있는 구",
  trdar_population: "주변을 오가는 사람",
  trdar_vitality: "동네 상권 흐름",
  online_attention: "블로그 언급",
  peer_competition: "주변 같은 업종 가게 수",
  peer_sales: "주변 같은 업종 매출",
  rent_level: "임대료 수준",
};

export const SENSITIVE_NOTE = "비교 기준을 바꾸면 방향이 달라질 수 있는 신호예요.";

// ---- 작은 도구들 ----
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function hasBatchim(word: string): boolean {
  const c = word.charCodeAt(word.length - 1);
  if (c < 0xac00 || c > 0xd7a3) return false;
  return (c - 0xac00) % 28 !== 0;
}
const ieyo = (w: string) => `${w}${hasBatchim(w) ? "이에요" : "예요"}`;
const eunNeun = (w: string) => `${w}${hasBatchim(w) ? "은" : "는"}`;

export function ageText(months: number): string {
  if (months < 12) return `${months}개월`;
  const y = Math.floor(months / 12);
  const m = months % 12;
  return m ? `${y}년 ${m}개월` : `${y}년`;
}

/** 업력(개월) = as_of와 인허가일의 연·월 차이 (REPORT_SCHEMA §5와 같은 식) */
export function monthsBetween(from: string | null, to: string): number | null {
  if (!from) return null;
  const [fy, fm] = from.split("-").map(Number);
  const [ty, tm] = to.split("-").map(Number);
  const m = (ty - fy) * 12 + (tm - fm);
  return m >= 0 ? m : null;
}

const signal = (up: boolean) => `모형은 이 점을 위험을 ${up ? "높이는" : "낮추는"} 신호로 봤어요.`;

function levelOf(v: number): Level {
  const a = Math.round(Math.abs(v) * 1000) / 1000; // 소수 셋째 자리 기준 (경계값 흔들림 방지)
  if (a >= 0.03) return "strong";
  if (a >= 0.01) return "some";
  if (a >= 0.001) return "slight";
  return "none";
}
const LEVEL_WORD = { strong: "크게", some: "조금", slight: "약간" } as const;
function levelText(level: Level, up: boolean): string {
  if (level === "none") return "영향이 거의 없어요";
  return `위험을 ${LEVEL_WORD[level]} ${up ? "높이는" : "낮추는"} 쪽이에요`;
}

function peerText(f: Factor): string | null {
  if (f.contribution <= 0 || f.peer_percentile == null || f.peer_percentile < 70) return null;
  const k = Math.max(1, Math.round((100 - f.peer_percentile) / 10));
  return `비슷한 가게 10곳 중 ${k}곳 정도로 두드러져요`;
}

// 온라인 근거(driver)는 서빙의 고정 템플릿 문구다 (REPORT_SCHEMA §5 표). 사실만 뽑아 쓴다.
function onlineFact(driver: string | null): string {
  if (!driver) return "";
  let m: RegExpMatchArray | null;
  if ((m = driver.match(/^마지막 블로그 언급 이후 (\d+)개월$/))) return `마지막 블로그 언급이 ${m[1]}개월 전이에요. `;
  if ((m = driver.match(/^최근 6개월 블로그 언급이 그 전 6개월보다 (\d+)건 줄어듦$/))) return `최근 6개월 블로그 언급이 그 전보다 ${m[1]}건 줄었어요. `;
  if ((m = driver.match(/^최근 6개월 블로그 언급이 그 전 6개월보다 (\d+)건 늘어남$/))) return `최근 6개월 블로그 언급이 그 전보다 ${m[1]}건 늘었어요. `;
  if (/^블로그 언급 이력 없음$/.test(driver)) return "아직 블로그 언급이 없어요. ";
  if (/^최근 12개월 블로그 언급 없음$/.test(driver) || /^최근 (12|3)개월 블로그 언급 0건$/.test(driver)) return "최근 1년 사이 블로그 언급이 없어요. ";
  if ((m = driver.match(/^최근 (12|3)개월 블로그 언급 (\d+)건$/))) return `최근 ${m[1]}개월 동안 블로그 언급이 ${m[2]}건 있어요. `;
  if (/이번 달에도 블로그 언급 있음/.test(driver)) return "이번 달에도 블로그 언급이 있어요. ";
  return "";
}

/** 사실 한 문장(있으면) + 모형 신호 문장 */
function sentence(f: Factor, r: Report): string {
  const up = f.contribution > 0;
  const v = f.values ?? {};
  let fact = "";
  switch (f.factor_id) {
    case "tenure": {
      const m = num(v.age_months) ?? monthsBetween(r.store.license_date, r.as_of);
      if (m !== null) fact = m === 0 ? "기준일 분기에 문을 연 가게예요. " : `문을 연 지 ${ageText(m)} 됐어요. `;
      break;
    }
    case "store_profile": {
      const area = num(v.area);
      fact = area !== null ? `${Math.round(area)}㎡ 규모의 ${ieyo(r.store.biz_type)}. ` : `${ieyo(r.store.biz_type)}. `;
      break;
    }
    case "district":
      fact = `${r.store.gu}에 있는 가게예요. `;
      break;
    case "trdar_vitality": {
      const oper = num(v.trdar_oper_months_avg);
      if (oper !== null) fact = `이 상권 가게들은 평균 ${ageText(Math.round(oper))} 정도 운영되고 있어요. `;
      break;
    }
    case "online_attention":
      fact = onlineFact(f.driver);
      break;
    case "peer_competition": {
      const n = num(v.trdar_biz_store_cnt_observed);
      if (n !== null) fact = `같은 상권에 같은 업종 가게가 ${Math.round(n)}곳 있어요. `;
      break;
    }
    case "trdar_population":
      fact = "주변 상권의 유동·상주·직장 인구를 봤어요. ";
      break;
    case "peer_sales":
      // 매출은 상권 단위 추정치(부분관측, 하한)이고 이 가게의 매출이 아니다. 금액은 쓰지 않는다
      fact = "같은 상권 같은 업종의 추정 매출 수준을 봤어요. ";
      break;
    case "rent_level":
      return "임대료 수준은 자료가 부족해 판단하지 않았어요.";
  }
  // 사실 문장이 없으면 '이 점' 대신 카드 제목(요인)을 가리킨다
  return fact ? fact + signal(up) : `모형은 이 요소를 위험을 ${up ? "높이는" : "낮추는"} 신호로 봤어요.`;
}

const MISSING_TEXT: Record<MissingReason, string> = {
  out_of_trdar: "이 가게는 서울시 상권 구역 밖에 있어서, 주변 상권 정보로는 판단하지 않았어요.",
  trdar_unknown: "이 가게가 속한 상권을 알 수 없어서, 주변 상권 정보로는 판단하지 않았어요.",
  trdar_quarter_unavailable: "이번 분기 상권 자료가 없어 주변 상권 일부는 판단하지 않았어요.",
  industry_unpublished: "이 상권에는 같은 업종 자료가 공개되지 않아 경쟁 상황은 판단하지 않았어요.",
  sales_unpublished: "이 상권에는 같은 업종 매출 자료가 공개되지 않아 매출 흐름은 판단하지 않았어요.",
  online_unobservable: "상호가 짧아 온라인 언급을 정확히 찾을 수 없어 이 부분은 판단하지 않았어요.",
  unknown: "일부 자료가 없어 판단하지 않은 부분이 있어요.",
};

function toOwner(f: Factor, r: Report): OwnerFactor {
  const up = f.contribution > 0;
  const level = f.direction === "영향 미미" ? "none" : levelOf(f.contribution);
  return {
    factorId: f.factor_id,
    label: LABEL[f.factor_id],
    direction: up ? "up" : "down",
    level,
    levelText: levelText(level, up),
    sentence: level === "none" ? `${eunNeun(LABEL[f.factor_id])} 이 가게의 위험 수준에 영향이 거의 없었어요.` : sentence(f, r),
    peerText: peerText(f),
    sensitive: f.interpretation_sensitive === true,
  };
}

/** 리포트 한 건의 위험요인을 사장님 화면용으로 바꾼다 */
export function toOwnerFactors(r: Report): OwnerFactorView {
  // 표시: display=true & hold_reason=null. 검토 대기(online_review)는 숨김. 데이터 없음은 사유 문장으로.
  const shown = r.factors.filter((f) => f.display && f.hold_reason === null);
  const missing = r.factors.filter((f) => f.hold_reason === "data_missing");

  const up = shown.filter((f) => f.contribution > 0 && f.direction !== "영향 미미").map((f) => toOwner(f, r));
  const down = shown
    .filter((f) => f.contribution < 0 && f.direction !== "영향 미미")
    .sort((a, b) => a.contribution - b.contribution) // 많이 낮춘 순
    .map((f) => toOwner(f, r));

  const reasons = [...new Set(missing.map((f) => f.missing_reason ?? "unknown"))] as MissingReason[];
  // 상권 밖이면 상권 요인 문장 하나로 충분하다
  const trdarWhole = reasons.includes("out_of_trdar") || reasons.includes("trdar_unknown");
  const missingNotes = reasons
    .filter((c) => !(trdarWhole && ["trdar_quarter_unavailable", "industry_unpublished", "sales_unpublished"].includes(c)))
    .map((c) => MISSING_TEXT[c]);

  const maxUp = Math.max(0, ...shown.map((f) => f.contribution));
  return {
    up,
    down,
    missingNotes,
    unavailableNote: r.unavailable_categories.includes("비용") ? "임대료 같은 비용 부담은 자료가 부족해 판단하지 않았어요." : null,
    noStandout: Math.round(maxUp * 1000) / 1000 < 0.01, // 높이는 요인이 모두 '약간' 이하
  };
}
