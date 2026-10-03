import ReportPage from "@/components/report/ReportPage";
import { storeIdParams } from "@/lib/server/bundleParams";

// 정적 export(STATIC_EXPORT=1)를 위해 번들의 점포 목록으로 미리 페이지를 만든다
export const dynamicParams = false;
export function generateStaticParams() {
  return storeIdParams();
}

export default function Page() {
  return <ReportPage />;
}
