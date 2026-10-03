"use client";

// 피그마 '리포트'(146:6440)를 공개 리포트 계약(public-static-0.1)·제출용 사례 계약(submission-static-0.1)으로 구현.
// 레이아웃·색·카드 모양은 피그마 그대로, 문구와 숫자 자리만 #48 검토 의견(M1~M7)·Issue #49 결정대로 바꿨다.
// 화면은 ScreenModel(lib/screenModel.ts)만 읽는다. 표시 금지 필드(factors[].explanation·summary_text,
// policies[].linked_factor_ids, online_presence, risk.model, risk.calibrated)는 모델에 들어오지 않는다.

import Link from "next/link";
import { useState } from "react";
import { SENSITIVE_NOTE, type OwnerFactor } from "@/lib/ownerText";
import type { ResponseItem } from "@/lib/actions";
import type { PolicyCardData, ScreenModel } from "@/lib/screenModel";
import { LogoSmall } from "../Logo";

const BANDS = [
  { k: "low", label: "낮음" },
  { k: "mid", label: "주의" },
  { k: "high", label: "높음" },
] as const;

const ymdLabel = (d: string) => {
  const [y, m, day] = d.split("-");
  return `${y}년 ${Number(m)}월 ${Number(day)}일`;
};
const mdLongLabel = (d: string) => {
  const [, m, day] = d.split("-");
  return `${Number(m)}월 ${Number(day)}일`;
};
const mdLabel = (d: string) => {
  const [, m, day] = d.split("-");
  return `${Number(m)}/${Number(day)}`;
};

export default function ReportView({ m }: { m: ScreenModel }) {
  const view = m.factors;
  const responses = m.responses;
  const [showAll, setShowAll] = useState(false);

  const top = view.up.slice(0, 3);
  const rest = [...view.up.slice(3), ...view.down];
  const bandLabel = BANDS.find((b) => b.k === m.risk.band)?.label ?? "-";
  const checkedDates = [...new Set(m.policies.map((p) => p.checkedAt).filter((d): d is string => !!d))].sort();

  return (
    <main className="mx-auto w-full max-w-screen pb-[calc(48px+env(safe-area-inset-bottom))] pt-[env(safe-area-inset-top)] text-white">
      {/* 상단 바 */}
      <header className="flex h-12 items-center px-[33px]">
        <Link href="/" aria-label="처음 화면으로">
          <LogoSmall />
        </Link>
      </header>

      <p className="mx-[29px] mt-1 rounded-[10px] border border-dashed border-[#555a6e] px-4 py-2 text-[12px] leading-[18px] text-[#a9adbd]">
        {m.banner}
      </p>

      {/* 가게 정보 카드 (146:6452) */}
      <section className="px-[29px] pt-[14px]">
        <div className="rounded-[10px] bg-[#3a3a3a] pb-[18px] pl-[27px] pr-5 pt-[19px]">
          <h1 className="text-[20px] font-semibold leading-[25px] tracking-[-0.4px]">{m.title}</h1>
          {m.statusNote && <p className="mt-2 text-[13px] leading-[18px] text-[#f0b4b4]">{m.statusNote}</p>}
          <dl className="mt-[17px] flex flex-col gap-[6px] text-[15px] leading-5">
            {m.rows.map((row) => (
              <Row key={row.k} k={row.k} v={row.v} />
            ))}
          </dl>
        </div>
      </section>

      {/* 위험 수준 (146:6446·6447·185:569) — 개인 확률·범위·중간값은 공개 리포트에 없다 (M1) */}
      <section className="px-[30px] pt-[33px]">
        <p className="text-[22px] font-medium leading-9 tracking-[-0.6px]">향후 12개월 상대 위험 수준은</p>
        <p className="text-[22px] font-medium leading-9 tracking-[-0.6px]">
          <span className="text-[34px] font-bold text-[#9aaef4]">{bandLabel}</span>이에요
        </p>

        <div className="mt-[27px] grid h-[38px] grid-cols-3 overflow-hidden rounded-[10px] bg-[#3a3a3a]" role="img" aria-label={`위험 수준 3단계 중 ${bandLabel}`}>
          {BANDS.map((b, i) => (
            <div
              key={b.k}
              className={`${i < 2 ? "border-r border-[#2a2a2a]" : ""} ${b.k === m.risk.band ? "rounded-[10px]" : ""}`}
              style={b.k === m.risk.band ? { backgroundImage: "linear-gradient(245deg, #F0F3FB 1%, #9AAEF4 100%)" } : undefined}
            />
          ))}
        </div>
        <div className="mt-[6px] grid grid-cols-3 text-center text-[14px] font-semibold leading-6 tracking-[-0.28px] text-[#b4b4b4]" aria-hidden>
          {BANDS.map((b) => (
            <span key={b.k} className={b.k === m.risk.band ? "text-white" : ""}>
              {b.label}
            </span>
          ))}
        </div>

        <ul className="mt-[20px] flex list-disc flex-col gap-2 pl-6 text-[16px] leading-6 tracking-[-0.32px]">
          {m.risk.percentile !== null && (
            <li>
              같은 {m.risk.peer_group} 가게 중 위험 상위 <b className="font-semibold">{Math.max(1, 100 - m.risk.percentile)}%</b>예요.
            </li>
          )}
          <li className="text-[14px] leading-[22px] text-[#b4b4b4]">위험 수준은 모형이 비슷한 가게들과 비교한 상대적인 위치예요. 실제 폐업률이 아니에요.</li>
        </ul>
      </section>

      {/* 위험요인 (146:6465, 185:594·614·619) */}
      <section className="mt-[33px] bg-gradient-to-b from-[#0f1014] to-[#373e58] px-[30px] pb-[60px] pt-[41px]">
        <h2 className="text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
          대시가 사장님 가게의
          <br />
          주요 위험요인을 분석했어요
        </h2>
        <div className="mt-[26px] flex flex-col gap-[10px]">
          {view.noStandout && (
            <div className="rounded-[10px] bg-[#f0f3fb] p-[19px]">
              <p className="text-[18px] font-semibold leading-6 tracking-[-0.36px] text-black">특별히 두드러진 신호가 없었어요</p>
            </div>
          )}
          {top.map((f) => (
            <FactorCard key={f.factorId} f={f} />
          ))}
          {rest.length > 0 && (
            <>
              <button onClick={() => setShowAll((v) => !v)} className="mt-1 self-start text-[14px] font-semibold text-[#b4b4b4] underline underline-offset-2">
                {showAll ? "접기" : `나머지 요인 ${rest.length}개 보기`}
              </button>
              {showAll && rest.map((f) => <FactorCard key={f.factorId} f={f} />)}
            </>
          )}
        </div>

        {(view.missingNotes.length > 0 || view.unavailableNote) && (
          <ul className="mt-5 flex flex-col gap-2 text-[13px] leading-[18px] text-[#cfd3e4]">
            {view.missingNotes.map((t) => (
              <li key={t}>
                <Pill>데이터 없음</Pill> {t}
              </li>
            ))}
            {view.unavailableNote && (
              <li>
                <Pill>판단 불가</Pill> {view.unavailableNote}
              </li>
            )}
          </ul>
        )}
        <p className="mt-5 text-[12px] leading-[18px] text-[#9b9b9b]">{m.disclaimer}</p>
      </section>

      {/* 대응 방향 (146:6471, 185:684~686) — 번호·세로선·알약 배지 틀만 재사용 (M5) */}
      <section className="px-[30px] pt-[52px]">
        <h2 className="text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
          대응 방향을
          <br />
          두 가지로 정리했어요
        </h2>
        <Steps items={responses} />
      </section>

      <div className="mt-14 h-3 bg-[#232323]" />

      {/* 지원사업 (146:6477, 185:642·643) — 위험요인과 연결하지 않는다 (M6, T4).
          check_required는 '자격 조건이 맞는'으로 부르지 않고 카드마다 확인 못 한 조건 수를 밝힌다 (#54, DECISIONS_W3 8-4) */}
      <section className="pt-14">
        <h2 className="px-[30px] text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
          가게 업종·지역으로
          <br />
          찾은 지원사업이에요
        </h2>
        {m.policyMatching === "not_performed" ? (
          <p className="mt-[26px] px-[30px] text-[14px] leading-5 text-[#9b9b9b]">지원사업 정보를 준비하고 있어요.</p>
        ) : m.policies.length === 0 ? (
          <p className="mt-[26px] px-[30px] text-[14px] leading-5 text-[#9b9b9b]">지금 가게 조건으로 찾은 지원사업은 없어요.</p>
        ) : (
          <>
            <p className="mt-3 px-[30px] text-[14px] leading-5 text-[#9b9b9b]">
              위험요인과는 따로, 업종·지역·공고 자격 기준으로만 찾았어요. 신청 전에 공고를 꼭 확인해 주세요
              {checkedDates.length > 0 ? ` (${checkedDates.map(mdLongLabel).join(", ")} 확인)` : ""}.
            </p>
            <div className="no-scrollbar mt-[20px] flex snap-x gap-[5px] overflow-x-auto px-[30px]">
              {orderPolicies(m.policies).map((p) => (
                <PolicyCard key={p.id} p={p} />
              ))}
            </div>
          </>
        )}
      </section>

      {/* 하단 (146:6481) — M7 */}
      <footer className="px-[33px] pt-[45px] text-[14px] font-semibold leading-5 text-muted">
        <p>
          {ymdLabel(m.asOf)} 기준이에요. 인허가 정보·주변 상권 통계·블로그 언급 수 같은 공개 데이터로 만든 상대 위험 수준이에요. 가게 자체의 매출·비용 자료는
          쓰지 않았어요. 위험요인은 모형의 신호이며 원인이 아니에요.
        </p>
        <Link href={m.backLink.href} className="mt-6 inline-block underline underline-offset-2">
          {m.backLink.label}
        </Link>
      </footer>
    </main>
  );
}

/** '사업 정리·재기' 성격의 사업은 등급과 관계없이 항상 맨 끝 (#54 묶음 필드가 확정되면 purpose로 교체) */
function orderPolicies(ps: PolicyCardData[]): PolicyCardData[] {
  const isExit = (p: PolicyCardData) => /재기|폐업|정리/.test(`${p.purpose ?? ""} ${p.name}`);
  return [...ps.filter((p) => !isExit(p)), ...ps.filter(isExit)];
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-3">
      <dt className="shrink-0 font-medium tracking-[-0.3px] text-[#898989]">{k}</dt>
      <dd className="min-w-0 font-normal">{v}</dd>
    </div>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return <span className="mr-1 inline-block rounded-full bg-white/10 px-2 py-[1px] text-[11px] font-semibold text-[#f0f3fb]">{children}</span>;
}

function FactorCard({ f }: { f: OwnerFactor }) {
  const up = f.direction === "up";
  return (
    <div className="rounded-[10px] bg-[#f0f3fb] p-[19px]">
      <p className={`text-[14px] font-semibold leading-5 tracking-[-0.28px] ${up ? "text-[#373e58]" : "text-[#2f7a4f]"}`}>{f.levelText}</p>
      <p className="mt-[2px] flex flex-wrap items-center gap-2 text-[18px] font-semibold leading-6 tracking-[-0.36px] text-black">
        {f.label}
        {f.sensitive && (
          <span className="rounded-full border border-[#e6c27a] bg-[#fff1d6] px-2 py-[1px] text-[11px] font-semibold leading-4 tracking-normal text-[#8a5a00]">해석 민감</span>
        )}
      </p>
      <p className="mt-[10px] text-[14px] font-medium leading-5 tracking-[-0.28px] text-[#515151]">
        {f.sentence}
        {f.peerText && ` ${f.peerText}.`}
      </p>
      {f.sensitive && <p className="mt-[6px] text-[13px] font-medium leading-[18px] text-[#8a5a00]">{SENSITIVE_NOTE}</p>}
    </div>
  );
}

const APPLY_STATUS_TEXT = { open: "접수 중", closed: "접수 마감", unknown: "접수 상태 확인 필요" } as const;

function PolicyCard({ p }: { p: PolicyCardData }) {
  const meta = [
    p.operator,
    p.applyStatus ? APPLY_STATUS_TEXT[p.applyStatus] : null,
    p.applyStatus !== "closed" && p.applyEnd ? `마감 ${mdLabel(p.applyEnd)}` : null,
    p.checkedAt ? `확인일 ${mdLabel(p.checkedAt)}` : null,
  ].filter(Boolean);
  return (
    <article className="flex min-h-[187px] w-[169px] shrink-0 snap-start flex-col justify-between gap-[22px] rounded-[10px] bg-[#f0f3fb] p-5">
      <div className="flex flex-col gap-[6px]">
        <span aria-hidden className="flex size-10 items-center justify-center rounded-full bg-[#373e58] text-[15px] font-semibold text-[#f0f3fb]">
          {p.operator.replace(/^\(예시\)\s*/, "").slice(0, 1)}
        </span>
        <p className="text-[16px] font-semibold leading-5 text-black">{p.name}</p>
        <p className="text-[12px] font-medium leading-4 text-[#55596b]">{meta.join(" · ")}</p>
        {p.matchStatus === "check_required" ? (
          <p className="text-[12px] font-semibold leading-4 text-[#a83333]">
            {p.uncheckedCount > 0 ? `조건 ${p.uncheckedCount}개는` : "일부 조건은"} 저희가 알 수 없어요. 공고에서 한 번 확인해 주세요.
          </p>
        ) : (
          <p className="text-[12px] font-semibold leading-4 text-[#2f7a4f]">사장님 가게 정보로 보면 신청 조건에 맞아요.</p>
        )}
      </div>
      {p.link ? (
        <a href={p.link} target="_blank" rel="noreferrer" className="flex h-[39px] w-[129px] items-center justify-center rounded-[10px] bg-[#373e58] text-[14px] font-semibold text-white">
          공고 원문 보기
        </a>
      ) : (
        <span className="flex h-[39px] w-[129px] items-center justify-center rounded-[10px] bg-[#b4b4b4] text-[14px] font-semibold text-white">원문 링크 없음</span>
      )}
    </article>
  );
}

const BADGE = {
  unconfirmed: { border: "#9a7432", dot: "#e9b46a", text: "#f0c987" },
  policy: { border: "#5d6aa0", dot: "#9aaef4", text: "#d2dafc" },
  pending: { border: "#898989", dot: "#898989", text: "#d0d0d0" },
} as const;

function Steps({ items }: { items: ResponseItem[] }) {
  return (
    <ol className="relative mt-[26px]">
      {items.length > 1 && <span aria-hidden className="absolute bottom-[40px] left-[18.5px] top-[13px] w-px bg-[#373e58]" />}
      {items.map((a, i) => {
        const b = BADGE[a.badge];
        return (
          <li key={a.id} className="relative flex gap-[19px] pb-10 last:pb-0">
            <span className="relative z-[1] ml-[6px] flex size-[26px] shrink-0 items-center justify-center rounded-[13px] bg-[#373e58] text-[16px] font-semibold leading-5 text-[#f0f3fb]">
              {i + 1}
            </span>
            <div className="flex flex-col gap-[10px] pt-[3px]">
              <div className="flex flex-col gap-[6px] font-medium">
                <p className="text-[16px] leading-5 text-white">{a.title}</p>
                <p className="text-[14px] leading-[18px] tracking-[-0.28px] text-[#9b9b9b]">{a.desc}</p>
              </div>
              <span className="inline-flex h-[31px] items-center gap-[7px] self-start rounded-[20.5px] border bg-[#22252f] px-3 text-[12px] font-semibold tracking-[-0.24px]" style={{ borderColor: b.border, color: b.text }}>
                <span aria-hidden className="size-[9px] rounded-full" style={{ background: b.dot }} />
                {a.badgeText}
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
