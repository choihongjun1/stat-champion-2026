"use client";

import { useEffect, useState } from "react";
import Splash from "@/components/Splash";
import SearchScreen from "@/components/SearchScreen";
import CaseList from "@/components/CaseList";
import { loadSubmissionMeta } from "@/lib/bundle";
import type { SubmissionMeta } from "@/lib/reportTypes";

const SPLASH_MS = 1600;

export default function Home() {
  // 온보딩은 탭(세션)마다 한 번만 보여준다
  const [phase, setPhase] = useState<"splash" | "leaving" | "done">("splash");
  // 제출용 비식별 사례 번들이면 검색 대신 사례 목록 (검색 색인은 제출 범위가 아니다)
  const [mode, setMode] = useState<{ kind: "pending" } | { kind: "search" } | { kind: "cases"; meta: SubmissionMeta }>({ kind: "pending" });

  useEffect(() => {
    loadSubmissionMeta()
      .then((meta) => setMode(meta ? { kind: "cases", meta } : { kind: "search" }))
      .catch(() => setMode({ kind: "search" })); // 검색 화면이 불러오기 오류를 보여준다
  }, []);

  useEffect(() => {
    let seen = false;
    try {
      seen = sessionStorage.getItem("dash_splash_seen") === "1";
      sessionStorage.setItem("dash_splash_seen", "1");
    } catch {}
    if (seen) return setPhase("done");
    const t1 = setTimeout(() => setPhase("leaving"), SPLASH_MS);
    const t2 = setTimeout(() => setPhase("done"), SPLASH_MS + 300);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  return (
    <>
      {mode.kind === "search" && <SearchScreen />}
      {mode.kind === "cases" && <CaseList meta={mode.meta} />}
      {phase !== "done" && <Splash leaving={phase === "leaving"} />}
    </>
  );
}
