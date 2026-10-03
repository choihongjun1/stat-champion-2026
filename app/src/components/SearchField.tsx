"use client";

import { forwardRef, useState } from "react";
import { ClearIcon, SearchIcon } from "./icons";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
};

// 피그마 검색_default(24:686) / focus(29:796) / filled(32:5640) 세 상태를 하나의 입력창으로 구현
// - 기본: 안내 문구 + 돋보기
// - 입력 중(빈 칸): 안내 문구·아이콘 없이 커서만
// - 입력됨: 흰 글씨 + 지우기(x) 버튼
const SearchField = forwardRef<HTMLInputElement, Props>(function SearchField({ value, onChange, onSubmit }, ref) {
  const [focused, setFocused] = useState(false);
  const empty = value.length === 0;

  return (
    <form
      role="search"
      className="relative h-[55px] w-full overflow-hidden rounded-[10px] bg-field"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <label htmlFor="store-search" className="sr-only">
        상호명과 동으로 가게 검색
      </label>
      <input
        ref={ref}
        id="store-search"
        type="search"
        inputMode="search"
        enterKeyHint="search"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={focused ? "" : "상호명+동으로 검색해주세요"}
        className="h-full w-full appearance-none bg-transparent pl-5 pr-14 text-[16px] font-semibold leading-5 text-white caret-white outline-none placeholder:text-hint [&::-webkit-search-cancel-button]:hidden"
      />
      {!empty ? (
        <button
          type="button"
          aria-label="검색어 지우기"
          onMouseDown={(e) => e.preventDefault()} // 지울 때 키보드가 내려가지 않도록
          onClick={() => onChange("")}
          className="absolute right-5 top-1/2 -translate-y-1/2"
        >
          <ClearIcon />
        </button>
      ) : !focused ? (
        <button type="submit" aria-label="검색" className="absolute right-5 top-1/2 -translate-y-1/2">
          <SearchIcon />
        </button>
      ) : null}
    </form>
  );
});

export default SearchField;
