"use client";

// 제출용 비식별 사례 번들의 첫 화면. 검색 색인이 없으므로(제출 범위 밖, R5) 사례 A/B/C 목록만 보여준다.
// 목록에는 구·업종만 쓴다 — 등급·순위는 사례 화면에서만 보여준다.

import Link from "next/link";
import { useEffect, useState } from "react";
import type { SubmissionCase, SubmissionMeta } from "@/lib/reportTypes";
import { loadCase } from "@/lib/bundle";
import { LogoSmall } from "./Logo";

type Row = { label: string; publicId: string; title: string; sub: string | null };

export default function CaseList({ meta }: { meta: SubmissionMeta }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    Promise.all(meta.cases.map((s) => (s.selection_status === "selected" ? loadCase(s.case_label) : Promise.resolve(null))))
      .then((cs) =>
        setRows(
          meta.cases.map((s, i) => {
            const c: SubmissionCase | undefined = cs[i]?.c;
            return c
              ? { label: s.case_label, publicId: s.public_id, title: c.case_title, sub: `서울특별시 ${c.store.gu} · ${c.store.biz_type}` }
              : { label: s.case_label, publicId: s.public_id, title: `사례 ${s.case_label}`, sub: null };
          }),
        ),
      )
      .catch(() => setLoadError(true));
  }, [meta]);

  return (
    <main className="mx-auto w-full max-w-screen px-[27px] pb-12 pt-[calc(131px+env(safe-area-inset-top))]">
      <LogoSmall className="ml-2" />
      <h1 className="ml-2 mt-6 text-[30px] font-medium leading-[38px] tracking-[-0.6px] text-white">
        비식별 실제 사례로
        <br />
        대시 화면을 보여드릴게요
      </h1>
      <p className="ml-2 mt-4 text-[14px] leading-5 text-sub">가게 이름과 주소는 표시하지 않아요.</p>

      {loadError && <p className="mt-4 pl-3 text-[15px] leading-5 text-sub">사례를 불러오지 못했어요. 새로고침해 주세요.</p>}

      {rows && (
        <ul className="mt-[37px]">
          {rows.map((r) => (
            <li key={r.label} className="border-b-[0.5px] border-muted last:border-b-0">
              {r.sub ? (
                <Link href={`/case/${encodeURIComponent(r.publicId)}`} className="block h-[87px] px-3 pt-[22px] active:bg-white/5">
                  <span className="block truncate text-[17px] font-medium leading-5 text-white">{r.title}</span>
                  <span className="mt-1 block truncate text-[15px] font-normal leading-5 text-sub">{r.sub}</span>
                </Link>
              ) : (
                <div className="block h-[87px] px-3 pt-[22px]">
                  <span className="block truncate text-[17px] font-medium leading-5 text-muted">{r.title}</span>
                  <span className="mt-1 block truncate text-[15px] font-normal leading-5 text-muted">적합한 비식별 실제 사례 없음</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
