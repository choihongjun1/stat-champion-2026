"use client";

// 제출용 비식별 사례(submission-static-0.1) 화면. 리포트와 같은 ReportView를 쓴다.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { CaseLabel } from "@/lib/reportTypes";
import { loadCase } from "@/lib/bundle";
import { fromCase, type ScreenModel } from "@/lib/screenModel";
import ReportView from "@/components/report/ReportView";
import LoadingScreen from "@/components/LoadingScreen";

const MIN_LOADING_MS = 1200;

export default function CasePage() {
  const { caseId } = useParams<{ caseId: string }>();
  const label = decodeURIComponent(caseId).replace(/^CASE-/, "") as CaseLabel;
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ok"; m: ScreenModel } | { kind: "missing" } | { kind: "error" }>({ kind: "loading" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setState({ kind: "loading" });
    const started = Date.now();
    loadCase(label)
      .then(async (res) => {
        await new Promise((r) => setTimeout(r, Math.max(0, MIN_LOADING_MS - (Date.now() - started))));
        setState(res ? { kind: "ok", m: fromCase(res.c) } : { kind: "missing" });
      })
      .catch(() => setState({ kind: "error" }));
  }, [label, retry]);

  if (state.kind === "loading") return <LoadingScreen />;
  if (state.kind === "ok") return <ReportView m={state.m} />;

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-screen flex-col items-center justify-center px-[30px] text-center">
      <p className="text-[20px] font-medium leading-[30px] tracking-[-0.4px] text-white">
        {state.kind === "missing" ? "적합한 비식별 실제 사례가 없어요" : "사례를 불러오지 못했어요"}
      </p>
      {state.kind === "error" && (
        <button onClick={() => setRetry((n) => n + 1)} className="mt-6 rounded-[10px] bg-[#373e58] px-6 py-3 text-[15px] font-semibold text-white">
          다시 시도
        </button>
      )}
      <Link href="/" className="mt-6 text-[16px] font-semibold text-muted underline underline-offset-2">
        사례 목록으로
      </Link>
    </main>
  );
}
