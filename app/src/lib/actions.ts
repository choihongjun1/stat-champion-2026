// '대응 방향' 두 축 (#48 검토 의견 M5, Issue #49 T1·T3·A1)
// ① 분석 근거: 전국 조사(소상공인실태조사 2023) 분석이 A1 게이트를 넘지 못해 모든 가게에서 '확인 불가'.
//    공개 리포트의 prescriptions는 항상 빈 배열이다 → 고정 문구.
// ② 지원사업: 자격 조건이 맞는 사업 수 (맞춤 묶음 = match_status가 matched 또는 check_required).
// 프론트가 근거 없이 만들던 점검 항목(지도 앱 등록 등)과 효과 배지는 제출 화면에서 뺐다 (T3).

import type { Report } from "./reportTypes";

export type ResponseBadge = "unconfirmed" | "policy" | "pending";
export type ResponseItem = { id: string; title: string; desc: string; badge: ResponseBadge; badgeText: string };

/** 맞춤 묶음 사업 수 (#54에서 묶음 필드가 정해지면 그 기준으로 바꾼다) */
export function matchedPolicyCount(r: Report): number {
  return r.policies.filter((p) => p.match_status === "matched" || p.match_status === "check_required").length;
}

export function buildResponses(r: Report): ResponseItem[] {
  const evidence: ResponseItem = {
    id: "evidence",
    title: "분석 근거",
    desc: "전국 조사 자료로 분석했지만, 관계의 방향을 확인할 근거가 충분하지 않아 대응 근거로 표시하지 않아요.",
    badge: "unconfirmed",
    badgeText: "확인 불가",
  };

  const n = matchedPolicyCount(r);
  const policy: ResponseItem =
    r.policy_matching === "not_performed"
      ? { id: "policy", title: "지원사업", desc: "지원사업 정보를 준비하고 있어요.", badge: "pending", badgeText: "정보 준비 중" }
      : {
          id: "policy",
          title: "지원사업",
          desc: n > 0 ? "신청 전 공식 공고를 꼭 확인하세요." : "지금 가게 조건으로 찾은 지원사업은 없어요.",
          badge: "policy",
          badgeText: `자격 조건이 맞는 사업 ${n}건`,
        };

  return [evidence, policy];
}
