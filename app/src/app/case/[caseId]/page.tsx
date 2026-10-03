import CasePage from "@/components/report/CasePage";
import { caseParams } from "@/lib/server/bundleParams";

// 제출용 비식별 사례 A/B/C (submission-static-0.1). 세 칸은 계약에 고정돼 있다 — 선택되지 않은 칸은 '사례 없음'을 보여준다
export const dynamicParams = false;
export function generateStaticParams() {
  return caseParams();
}

export default function Page() {
  return <CasePage />;
}
