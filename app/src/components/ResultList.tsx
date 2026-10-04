import Link from "next/link";
import type { SearchEntry } from "@/lib/reportTypes";

// 피그마 검색_results(32:5773) / 검색_results_결과없음(106:6239)
// synthetic=false(시연 비식별 번들)면 주소 없이 '이름 · 구 업종'만 보여준다 (D4)
export default function ResultList({ items, onNotHere, synthetic }: { items: SearchEntry[]; onNotHere: () => void; synthetic: boolean }) {
  return (
    <section aria-label="검색결과">
      <p className="pl-3 text-[15px] font-semibold leading-5 text-muted">검색결과</p>
      {items.length === 0 ? (
        <div className="mt-[139px] text-center">
          <button onClick={onNotHere} className="text-[16px] font-semibold leading-5 text-muted underline underline-offset-2">
            검색결과가 없어요
          </button>
        </div>
      ) : (
        <>
          <ul className="-mt-[5px]">
            {items.map((s) => (
              <li key={s.store_id} className="border-b-[0.5px] border-muted last:border-b-0">
                <Link href={`/report/${encodeURIComponent(s.store_id)}`} className="block h-[87px] px-3 pt-[22px] active:bg-white/5">
                  <span className="block truncate text-[17px] font-medium leading-5 text-white">{s.name ?? `${s.gu} ${s.biz_type}`}</span>
                  <span className="mt-1 block truncate text-[15px] font-normal leading-5 text-sub">
                    {synthetic ? s.address_road ?? s.address_jibun ?? `${s.gu} ${s.biz_type}` : `${s.gu} ${s.biz_type}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-[29px] text-center">
            <button onClick={onNotHere} className="text-[16px] font-semibold leading-5 text-muted underline underline-offset-2">
              여기에 없어요
            </button>
          </div>
        </>
      )}
    </section>
  );
}
