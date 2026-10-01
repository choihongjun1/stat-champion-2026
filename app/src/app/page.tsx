"use client";

import { useEffect, useState } from "react";
import Splash from "@/components/Splash";
import SearchScreen from "@/components/SearchScreen";

const SPLASH_MS = 1600;

export default function Home() {
  // 온보딩은 탭(세션)마다 한 번만 보여준다
  const [phase, setPhase] = useState<"splash" | "leaving" | "done">("splash");

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
      <SearchScreen />
      {phase !== "done" && <Splash leaving={phase === "leaving"} />}
    </>
  );
}
