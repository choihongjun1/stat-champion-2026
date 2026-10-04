// 리포트 화면(ReportView)이 그리는 값. 전체 점포 리포트(public-static-0.1)와 제출용 비식별 사례(submission-static-0.1)를
// 같은 화면으로 그리기 위한 변환 층이다. 화면은 이 모델만 읽고, 계약에 없는 값을 지어내지 않는다.

import type { Report, SubmissionCase } from "./reportTypes";
import { ageText, monthsBetween, toOwnerFactors, toOwnerFactorsFromCase, type OwnerFactorView } from "./ownerText";
import { buildResponses, type ResponseItem } from "./actions";

/** 지원사업 카드 — 위험요인과 연결하는 필드(linked_factor_ids)는 넣지 않는다 (T4) */
export type PolicyCardData = {
  id: string;
  name: string;
  operator: string;
  link: string | null;
  purpose: string | null;
  matchStatus: "matched" | "check_required";
  uncheckedCount: number; // 저희가 확인할 수 없는 자격 조건 수
  applyStatus: "open" | "closed" | "unknown" | null; // null = 계약에 없음 (W2 합성 샘플)
  applyEnd: string | null;
  checkedAt: string | null;
};

export type ScreenModel = {
  banner: string;
  title: string;
  statusNote: string | null;
  rows: { k: string; v: string }[];
  asOf: string;
  risk: { band: "low" | "mid" | "high"; percentile: number | null; peer_group: string };
  factors: OwnerFactorView;
  responses: ResponseItem[];
  policyMatching: "performed" | "not_performed";
  policies: PolicyCardData[];
  disclaimer: string;
  backLink: { href: string; label: string };
};

const ymLabel = (d: string) => {
  const [y, m] = d.split("-");
  return `${y}년 ${Number(m)}월`;
};

const SYNTHETIC_BANNER = "화면 확인용 합성 예시예요.";
const DEMO_BANNER = "시연용 비식별 실제 사례예요. 가게 이름과 주소는 표시하지 않아요.";

export function fromReport(r: Report, synthetic: boolean): ScreenModel {
  const months = monthsBetween(r.store.license_date, r.as_of);
  // 시연(비식별) 번들은 위치를 구까지만 보여준다 (T5·R2)
  const location = synthetic ? r.store.address_road ?? r.store.address_jibun ?? `서울특별시 ${r.store.gu}` : `서울특별시 ${r.store.gu}`;
  const policies: PolicyCardData[] = r.policies.map((p) => ({
    id: p.id,
    name: p.name,
    operator: p.operator,
    link: p.link,
    purpose: p.purpose ?? null,
    matchStatus: p.match_status,
    uncheckedCount: p.unverifiable_conditions.length,
    applyStatus: p.apply_status === "open" || p.apply_status === "closed" || p.apply_status === "unknown" ? p.apply_status : null,
    applyEnd: p.apply_end ?? null,
    checkedAt: p.collected_at,
  }));
  return {
    banner: synthetic ? SYNTHETIC_BANNER : DEMO_BANNER,
    title: r.store.name ?? `${r.store.gu} ${r.store.biz_type}`,
    statusNote:
      r.store.status.current === "open"
        ? null
        : r.store.status.current === "closed"
          ? `${ymLabel(r.as_of)} 기준일 이후 폐업 신고가 확인된 가게예요.`
          : "현재 영업 상태를 확인할 수 없어요.",
    rows: [
      { k: "위치", v: location },
      ...(months !== null ? [{ k: "업력", v: `${ymLabel(r.as_of)} 기준 ${ageText(months)}` }] : []),
      { k: "업종", v: r.store.biz_type },
    ],
    asOf: r.as_of,
    risk: { band: r.risk.band, percentile: r.risk.percentile, peer_group: r.risk.peer_group },
    factors: toOwnerFactors(r),
    responses: buildResponses(r.policy_matching, policies),
    policyMatching: r.policy_matching,
    policies,
    disclaimer: r.disclaimer,
    backLink: { href: "/", label: "다른 가게 검색하기" },
  };
}

/** 제출용 비식별 사례. 계약에 있는 값만 쓴다 — 업력·영업 상태·주소는 계약에 없으므로 표시하지 않는다 */
export function fromCase(c: SubmissionCase): ScreenModel {
  const policies: PolicyCardData[] = c.policies.map((p) => ({
    id: p.id,
    name: p.name,
    operator: p.operator,
    link: p.link,
    purpose: null,
    matchStatus: p.match_status,
    uncheckedCount: p.unverified_condition_count,
    applyStatus: p.apply_status,
    applyEnd: p.apply_end,
    checkedAt: p.checked_at,
  }));
  return {
    banner: c.data_kind === "real" ? DEMO_BANNER : SYNTHETIC_BANNER,
    title: c.case_title,
    statusNote: null,
    rows: [
      { k: "위치", v: `서울특별시 ${c.store.gu}` },
      { k: "업종", v: c.store.biz_type },
    ],
    asOf: c.as_of,
    risk: { band: c.risk.band, percentile: c.risk.percentile, peer_group: c.risk.peer_group },
    factors: toOwnerFactorsFromCase(c),
    responses: buildResponses(c.policy_matching, policies),
    policyMatching: c.policy_matching,
    policies,
    disclaimer: c.disclaimer,
    backLink: { href: "/", label: "다른 사례 보기" },
  };
}
