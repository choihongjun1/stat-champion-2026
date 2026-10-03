import DongPending from "@/components/DongPending";
import { dongParams } from "@/lib/server/bundleParams";

export const dynamicParams = false;
export function generateStaticParams() {
  return dongParams();
}

export default function Page() {
  return <DongPending />;
}
