"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { DongEntry, SearchEntry } from "@/lib/reportTypes";
import { loadDongs, loadMeta, loadSearchIndex } from "@/lib/bundle";
import { GU_ORDER, dongsByGu, searchStores } from "@/lib/search";
import { LogoSmall } from "./Logo";
import SearchField from "./SearchField";
import ResultList from "./ResultList";
import AreaPicker from "./AreaPicker";

type View = { kind: "idle" } | { kind: "results"; items: SearchEntry[] } | { kind: "area" };

export default function SearchScreen() {
  const [index, setIndex] = useState<SearchEntry[] | null>(null);
  const [dongs, setDongs] = useState<DongEntry[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [synthetic, setSynthetic] = useState(true);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>({ kind: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  // 검색 색인·동 목록은 한 번만 받아온다 (백엔드 없음 — 정적 번들)
  useEffect(() => {
    Promise.all([loadSearchIndex(), loadDongs(), loadMeta()])
      .then(([idx, ds, meta]) => {
        setIndex(idx);
        setDongs(ds);
        setSynthetic(meta.data_kind === "synthetic_sample");
      })
      .catch(() => setLoadError(true));
  }, []);

  const byGu = useMemo(() => dongsByGu(dongs), [dongs]);
  const guList = GU_ORDER.filter((g) => byGu[g]?.length);

  const submit = () => {
    if (!index || !query.trim()) return;
    setView({ kind: "results", items: searchStores(query, index, dongs) });
    inputRef.current?.blur();
  };

  const changeQuery = (v: string) => {
    setQuery(v);
    if (v === "") setView({ kind: "idle" });
  };

  // '다시 검색할래요' → 검색_default(24:686): 검색어를 비우고 포커스 없이 처음 상태로
  const backToSearch = () => {
    setQuery("");
    setView({ kind: "idle" });
    window.scrollTo({ top: 0 });
  };

  return (
    <main className="mx-auto w-full max-w-screen px-[27px] pb-12 pt-[calc(131px+env(safe-area-inset-top))]">
      <LogoSmall className="ml-2" />
      <h1 className="ml-2 mt-6 text-[30px] font-medium leading-[38px] tracking-[-0.6px] text-white">
        사장님의 가게를
        <br />
        대시가 진단해드릴게요
      </h1>

      <div className="mt-[33px]">
        {view.kind === "area" ? (
          <AreaPicker
            guList={guList}
            dongs={byGu}
            // 동을 고르면 동 화면으로 이동 — 동 리포트는 release_ready=false라 10/5 회의 전까지 '준비 중'
            onPick={(gu, dong) => router.push(`/dong/${encodeURIComponent(gu)}/${encodeURIComponent(dong)}`)}
          />
        ) : (
          <SearchField ref={inputRef} value={query} onChange={changeQuery} onSubmit={submit} />
        )}
      </div>

      {loadError && <p className="mt-4 pl-3 text-[15px] leading-5 text-sub">가게 목록을 불러오지 못했어요. 새로고침해 주세요.</p>}

      {view.kind === "results" && (
        <div className="mt-[37px]">
          <ResultList items={view.items} synthetic={synthetic} onNotHere={() => setView({ kind: "area" })} />
        </div>
      )}

      {view.kind === "area" && (
        <div className="mt-[63px] text-center">
          <button onClick={backToSearch} className="text-[16px] font-semibold leading-5 text-muted underline underline-offset-2">
            다시 검색할래요
          </button>
        </div>
      )}
    </main>
  );
}
