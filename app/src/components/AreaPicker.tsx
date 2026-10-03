"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Props = {
  guList: string[];
  dongs: Record<string, string[]>;
  onPick: (gu: string, dong: string) => void;
};

// 피그마 검색_실패시 주소(42:5931) / 스크롤(49:6018)
// 왼쪽: 구 선택, 오른쪽: 법정동 목록(6칸 높이, 넘치면 스크롤 + 자체 스크롤 표시)
export default function AreaPicker({ guList, dongs, onPick }: Props) {
  const [gu, setGu] = useState(guList[0]);
  const listRef = useRef<HTMLDivElement>(null);
  const [bar, setBar] = useState<{ top: number; height: number } | null>(null);

  const updateBar = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    if (scrollHeight <= clientHeight + 1) return setBar(null);
    const height = Math.max(40, (clientHeight / scrollHeight) * clientHeight);
    const top = (scrollTop / (scrollHeight - clientHeight)) * (clientHeight - height);
    setBar({ top, height });
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: 0 });
    updateBar();
  }, [gu, updateBar]);

  return (
    <div className="relative flex h-[332px] w-full border-y border-muted">
      <div role="tablist" aria-label="구 선택" className="w-[130px] shrink-0 border-r border-muted">
        {guList.map((g) => {
          const selected = g === gu;
          return (
            <button
              key={g}
              role="tab"
              aria-selected={selected}
              onClick={() => setGu(g)}
              className={`block h-[55px] w-full text-center text-[16px] font-medium leading-5 tracking-[-0.32px] text-white ${
                selected ? "bg-field-active" : "border-b border-field-active bg-field"
              }`}
            >
              {g}
            </button>
          );
        })}
      </div>
      <div ref={listRef} onScroll={updateBar} role="tabpanel" aria-label={`${gu} 동 목록`} className="no-scrollbar flex-1 overflow-y-auto">
        <ul>
          {(dongs[gu] ?? []).map((d) => (
            <li key={d}>
              <button
                onClick={() => onPick(gu, d)}
                className="block h-[55px] w-full border-b border-field-active pl-[26px] text-left text-[16px] font-medium leading-5 tracking-[-0.32px] text-white active:bg-white/5"
              >
                {d}
              </button>
            </li>
          ))}
        </ul>
      </div>
      {bar && (
        <div
          aria-hidden
          className="pointer-events-none absolute right-[4px] w-[5px] rounded-[2.5px] bg-scroll"
          style={{ top: bar.top, height: bar.height }}
        />
      )}
    </div>
  );
}
