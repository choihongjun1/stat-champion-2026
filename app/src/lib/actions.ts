// '개선 방향' 목록 만들기
// - 서빙 prescriptions: W3 인과분석 전이라 비어 있거나 status=unavailable 항목만 온다 (REPORT_SCHEMA §6)
// - 프론트 규칙(효과 미확인): 사장님이 직접 관리할 수 있는 요인·현재 상태에서만 만든다
//   ① 온라인 후기 요인이 표시되고 위험을 높였을 때 ② 지도 앱 미등록(online_presence, 현재 스냅샷)
//   효과를 약속하지 않고, 배지로 '효과 미확인'을 항상 붙인다 (가이드라인 §10-3 근거 수준 표시)

import type { Report } from "./reportTypes";

export type ActionBadge = "verified" | "unverified" | "pending";
export type ActionItem = { id: string; title: string; desc: string; badge: ActionBadge };

export function buildActions(r: Report): ActionItem[] {
  const items: ActionItem[] = [];

  const online = r.factors.find((f) => f.factor_id === "online_attention");
  if (online && online.display && online.hold_reason === null && online.contribution >= 0.01) {
    items.push({
      id: "online_reviews",
      title: "온라인 후기 관리하기",
      desc: "최근 후기가 적은 편이에요. 방문 손님이 지도 앱이나 블로그에 후기를 남기기 쉽도록 안내해 보세요.",
      badge: "unverified",
    });
  }

  const op = r.online_presence;
  if (op) {
    const missing = [op.naver_local_registered === false ? "네이버 지도" : null, op.kakao_registered === false ? "카카오맵" : null].filter(Boolean);
    if (missing.length) {
      items.push({
        id: "map_listing",
        title: "지도 앱에 가게 정보 등록하기",
        desc: `지금 ${missing.join("과 ")}에서 가게 정보를 찾지 못했어요. 영업시간·메뉴·사진을 등록해 보세요.`,
        badge: "unverified",
      });
    }
  }

  for (const p of r.prescriptions) {
    items.push({ id: p.id, title: p.title, desc: p.unavailable_reason, badge: "pending" });
  }
  return items;
}
