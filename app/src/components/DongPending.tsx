"use client";

// 동 리포트는 공개 준비 전(dong_summary release_ready=false)이라 제출 화면에서 뺐다.
// 10/5 회의에서 제출 범위가 정해질 때까지 '준비 중'으로 끝낸다 (#48 검토 의견 R4, 안내서 4-1).
import Link from "next/link";
import { useParams } from "next/navigation";
import { LogoSmall } from "./Logo";

export default function DongPending() {
  const params = useParams<{ gu: string; dong: string }>();
  const gu = decodeURIComponent(params.gu);
  const dong = decodeURIComponent(params.dong);
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-screen flex-col px-[30px] pb-16 pt-[env(safe-area-inset-top)] text-white">
      <header className="flex h-12 items-center">
        <Link href="/" aria-label="처음 화면으로">
          <LogoSmall />
        </Link>
      </header>
      <div className="flex flex-1 flex-col items-center justify-center text-center">
        <p className="text-[15px] text-sub">
          {dong} ({gu})
        </p>
        <p className="mt-3 text-[20px] font-medium leading-[30px] tracking-[-0.4px]">동 리포트는 준비 중이에요</p>
        <Link href="/" className="mt-10 text-[16px] font-semibold text-muted underline underline-offset-2">
          다시 검색할래요
        </Link>
      </div>
    </main>
  );
}
