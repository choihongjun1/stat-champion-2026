"use client";

// ⚠️ 디자인 전 임시 '동 리포트' 화면. 데이터: dong_summary.json (REPORT_SCHEMA §8·§12)
// 숨긴 칸(소표본)은 수치를 보여주지 않고 note만 보여준다. note('개별 가게 진단 아님')는 항상 보여준다.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { DongSummaryRow } from "@/lib/reportTypes";
import { loadDongSummary } from "@/lib/bundle";
import { LogoSmall } from "@/components/Logo";

const BANDS = [
  { k: "high", label: "높음", color: "#9aaef4" },
  { k: "mid", label: "주의", color: "#5e6780" },
  { k: "low", label: "낮음", color: "#3a3a3a" },
] as const;

export default function DongReportPage() {
  const params = useParams<{ gu: string; dong: string }>();
  const gu = decodeURIComponent(params.gu);
  const dong = decodeURIComponent(params.dong);
  const [rows, setRows] = useState<DongSummaryRow[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    loadDongSummary()
      .then((all) => setRows(all.filter((r) => r.gu === gu && r.dong === dong)))
      .catch(() => setError(true));
  }, [gu, dong]);

  const whole = rows?.find((r) => r.biz_type === null);
  const byBiz = rows?.filter((r) => r.biz_type !== null) ?? [];

  return (
    <main className="mx-auto w-full max-w-screen px-[30px] pb-16 pt-[env(safe-area-inset-top)] text-white">
      <header className="flex h-12 items-center">
        <Link href="/" aria-label="처음 화면으로">
          <LogoSmall />
        </Link>
      </header>
      <p className="mt-2 rounded-[10px] border border-[#3a3a3a] px-4 py-2 text-[12px] leading-[18px] text-[#898989]">디자인 전 임시 화면이에요.</p>

      <h1 className="mt-8 text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
        {dong} ({gu})
      </h1>
      <p className="mt-3 text-[16px] leading-6 text-sub">
        사장님의 가게를 아직 분석하지 못했어요.
        <br />
        대신 주변 가게의 위험도 분포를 보여드릴게요!
      </p>

      {error && <p className="mt-8 text-sub">동 정보를 불러오지 못했어요.</p>}
      {rows && !whole && <p className="mt-8 text-sub">이 동의 분석 결과가 아직 없어요.</p>}

      {whole && (
        <section className="mt-8 rounded-[10px] bg-[#22212f] p-5">
          <h2 className="text-[15px] font-semibold text-muted">이 동 가게들의 위험 등급 분포</h2>
          {whole.suppressed || !whole.band_share ? (
            <p className="mt-3 text-[15px] leading-[22px] text-sub">가게 수가 적어 분포를 보여드리지 않아요.</p>
          ) : (
            <>
              <p className="mt-2 text-[15px] text-sub">가게 {whole.n_stores}곳 기준</p>
              <BandBar share={whole.band_share} />
              <p className="mt-4 text-[15px] leading-[22px]">
                {whole.top_risk_biz_types && whole.top_risk_biz_types.length > 0
                  ? `‘높음’ 등급 비율이 높은 업종: ${whole.top_risk_biz_types.join(", ")}`
                  : byBiz.some((b) => b.suppressed)
                    ? "업종별로 나누면 가게 수가 적어 업종 순위는 보여드리지 않아요."
                    : "‘높음’ 등급 가게가 있는 업종은 없어요."}
              </p>
            </>
          )}
        </section>
      )}

      {byBiz.length > 0 && (
        <section className="mt-4 flex flex-col gap-3">
          {byBiz.map((r) => (
            <div key={r.biz_type} className="rounded-[10px] bg-[#22212f] p-5">
              <h3 className="text-[16px] font-medium">{r.biz_type}</h3>
              {r.suppressed || !r.band_share ? (
                <p className="mt-2 text-[14px] text-sub">가게 수가 적어 보여드리지 않아요.</p>
              ) : (
                <>
                  <p className="mt-1 text-[14px] text-sub">가게 {r.n_stores}곳</p>
                  <BandBar share={r.band_share} />
                </>
              )}
            </div>
          ))}
        </section>
      )}

      {whole && <p className="mt-6 text-[13px] leading-5 text-muted">{whole.note}</p>}
      <p className="mt-2 text-[13px] leading-5 text-muted">위험 등급은 모형의 예측을 모은 값이며, 실제 폐업률이 아니에요.</p>

      <div className="mt-10 text-center">
        <Link href="/" className="text-[16px] font-semibold text-muted underline underline-offset-2">
          다시 검색할래요
        </Link>
      </div>
    </main>
  );
}

function BandBar({ share }: { share: { low: number; mid: number; high: number } }) {
  return (
    <div className="mt-3">
      <div className="flex h-3 overflow-hidden rounded-full" aria-hidden>
        {BANDS.map((b) => (
          <div key={b.k} style={{ width: `${share[b.k] * 100}%`, background: b.color }} />
        ))}
      </div>
      <p className="mt-2 flex gap-4 text-[13px] text-sub">
        {BANDS.map((b) => (
          <span key={b.k}>
            <span className="mr-1 inline-block size-2 rounded-full align-middle" style={{ background: b.color }} />
            {b.label} {Math.round(share[b.k] * 100)}%
          </span>
        ))}
      </p>
    </div>
  );
}
