"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { fromReport, type ScreenModel } from "@/lib/screenModel";
import { loadMeta, loadReport } from "@/lib/bundle";
import ReportView from "@/components/report/ReportView";
import LoadingScreen from "@/components/LoadingScreen";

// 파일은 금방 읽히지만 '진단 중' 화면이 깜빡이지 않게 최소 1.2초 보여준다
const MIN_LOADING_MS = 1200;

export default function ReportPage() {
  const { storeId } = useParams<{ storeId: string }>();
  const id = decodeURIComponent(storeId);
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ok"; m: ScreenModel } | { kind: "missing" } | { kind: "error" }>({ kind: "loading" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setState({ kind: "loading" });
    const started = Date.now();
    Promise.all([loadReport(id), loadMeta()])
      .then(async ([r, meta]) => {
        await new Promise((res) => setTimeout(res, Math.max(0, MIN_LOADING_MS - (Date.now() - started))));
        setState(r ? { kind: "ok", m: fromReport(r, meta.data_kind === "synthetic_sample") } : { kind: "missing" });
      })
      .catch(() => setState({ kind: "error" }));
  }, [id, retry]);

  if (state.kind === "loading") return <LoadingScreen />;
  if (state.kind === "ok") return <ReportView m={state.m} />;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-screen flex-col items-center justify-center px-[30px] text-center">
      <p className="text-[20px] font-medium leading-[30px] tracking-[-0.4px] text-white">
        {state.kind === "missing" ? "이 가게의 리포트를 찾지 못했어요" : "리포트를 불러오지 못했어요"}
      </p>
      {state.kind === "error" && (
        <button onClick={() => setRetry((n) => n + 1)} className="mt-6 rounded-[10px] bg-[#373e58] px-6 py-3 text-[15px] font-semibold text-white">
          다시 시도
        </button>
      )}
      <Link href="/" className="mt-6 text-[16px] font-semibold text-muted underline underline-offset-2">
        다시 검색할래요
      </Link>
    </main>
  );
}
