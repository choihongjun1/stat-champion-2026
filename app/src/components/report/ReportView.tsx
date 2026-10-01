"use client";

// 피그마 '리포트'(146:6440)를 스키마 0.2 데이터로 구현.
// 디자인과 다르게 둔 곳은 README '리포트 화면 메모' 참고 (문구 규칙·데이터 계약 때문).

import Link from "next/link";
import { useState } from "react";
import type { Report } from "@/lib/reportTypes";
import { ageText, monthsBetween, toOwnerFactors, type OwnerFactor, LABEL } from "@/lib/ownerText";
import { buildActions, type ActionItem } from "@/lib/actions";
import { LogoSmall } from "../Logo";

const BAND_LABEL = { low: "낮음", mid: "주의", high: "높음" } as const;
const pct0 = (v: number) => `${Math.round(v * 100)}%`;
const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;
const ymLabel = (d: string) => {
  const [y, m] = d.split("-");
  return `${y}년 ${Number(m)}월`;
};

export default function ReportView({ r, synthetic }: { r: Report; synthetic: boolean }) {
  const view = toOwnerFactors(r);
  const actions = buildActions(r);
  const [showAll, setShowAll] = useState(false);

  const months = monthsBetween(r.store.license_date, r.as_of);
  const top = view.up.slice(0, 3);
  const rest = [...view.up.slice(3), ...view.down];

  // 범위 막대 척도: 0 ~ S (S는 범위·중간값이 다 들어가게 10% 단위로 올림)
  const S = Math.ceil(Math.max(0.3, r.risk.ci_high * 1.2, r.risk.peer_median * 1.2) * 10) / 10;
  const x = (v: number) => `${Math.min(100, (v / S) * 100)}%`;

  return (
    <main className="mx-auto w-full max-w-screen pb-[calc(48px+env(safe-area-inset-bottom))] pt-[env(safe-area-inset-top)] text-white">
      {/* 상단 바 */}
      <header className="flex h-12 items-center px-[33px]">
        <Link href="/" aria-label="처음 화면으로">
          <LogoSmall />
        </Link>
      </header>

      {synthetic && (
        <p className="mx-[29px] mt-1 rounded-[10px] border border-[#3a3a3a] px-4 py-2 text-[12px] leading-[18px] text-[#898989]">
          화면 확인용 합성 예시예요. 실제 가게·실제 분석 결과가 아닙니다.
        </p>
      )}

      {/* 가게 정보 카드 */}
      <section className="px-[29px] pt-[14px]">
        <div className="rounded-[10px] bg-[#3a3a3a] pb-[18px] pl-[27px] pr-5 pt-[19px]">
          <h1 className="text-[20px] font-semibold leading-[25px] tracking-[-0.4px]">{r.store.name ?? "상호 정보 없음"}</h1>
          {r.store.status.current !== "open" && (
            <p className="mt-2 text-[13px] leading-[18px] text-[#f0b4b4]">
              {r.store.status.current === "closed"
                ? `${ymLabel(r.as_of)} 이후 폐업 신고가 확인된 가게예요${r.store.status.close_date ? ` (${r.store.status.close_date})` : ""}.`
                : "현재 영업 상태를 확인할 수 없어요."}
            </p>
          )}
          <dl className="mt-[17px] flex flex-col gap-[6px] text-[15px] leading-5">
            <Row k="위치" v={r.store.address_road ?? r.store.address_jibun ?? `${r.store.gu} ${r.store.dong ?? ""}`} />
            <Row k="업력" v={months !== null ? `${ymLabel(r.as_of)} 기준 ${ageText(months)}` : "인허가일 정보 없음"} />
            <Row k="업종" v={r.store.biz_type} />
          </dl>
        </div>
      </section>

      {/* 위험도 요약 */}
      <section className="px-[30px] pt-[33px]">
        <p className="text-[22px] font-medium leading-9 tracking-[-0.6px]">12개월 이내 폐업 위험도는</p>
        <p className="text-[22px] font-medium leading-9 tracking-[-0.6px]">
          <span className="text-[34px] font-bold text-[#9aaef4]">{pct0(r.risk.probability_12m)}</span>로 예측돼요
        </p>

        <div className="relative mt-[27px] h-[38px] rounded-[10px] bg-[#3a3a3a]" aria-hidden>
          <div
            className="absolute inset-y-0 rounded-[10px]"
            style={{
              left: x(r.risk.ci_low),
              width: `max(10px, calc(${x(r.risk.ci_high)} - ${x(r.risk.ci_low)}))`,
              backgroundImage: "linear-gradient(245deg, #F0F3FB 1%, #9AAEF4 100%)",
            }}
          />
          <div className="absolute -bottom-[5px] -top-[6px] w-px bg-[#b4b4b4]" style={{ left: x(r.risk.peer_median) }} />
        </div>
        <div className="relative h-6">
          <span className="absolute top-[2px] -translate-x-1/2 whitespace-nowrap text-[14px] font-semibold leading-6 tracking-[-0.28px] text-[#b4b4b4]" style={{ left: `clamp(40px, ${x(r.risk.peer_median)}, calc(100% - 40px))` }}>
            중간값 {pct0(r.risk.peer_median)}
          </span>
        </div>

        <ul className="mt-[26px] flex list-disc flex-col gap-2 pl-6 text-[16px] leading-6 tracking-[-0.32px]">
          <li>
            예측이 흔들릴 수 있는 범위는 <b className="font-semibold">{pct0(r.risk.ci_low)}~{pct0(r.risk.ci_high)}</b>이고, 위험 등급은 <b className="font-semibold">{BAND_LABEL[r.risk.band]}</b>이에요.
          </li>
          <li>
            {r.risk.peer_group} 가게들의 중간 위험도는 {pct1(r.risk.peer_median)}예요.
            {r.risk.percentile !== null && ` 이 가게는 위에서 ${Math.max(1, 100 - r.risk.percentile)}% 안에 들어요.`}
          </li>
        </ul>
        <p className="mt-3 text-[13px] leading-[18px] text-[#898989]">{r.risk.interval_note}</p>
      </section>

      {/* 위험요인 */}
      <section className="mt-[33px] bg-gradient-to-b from-[#0f1014] to-[#373e58] px-[30px] pb-[60px] pt-[41px]">
        <h2 className="text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
          대시가 사장님 가게의
          <br />
          주요 위험요인을 분석했어요
        </h2>
        <div className="mt-[26px] flex flex-col gap-[10px]">
          {view.noStandout && (
            <div className="rounded-[10px] bg-[#f0f3fb] p-[19px]">
              <p className="text-[18px] font-semibold leading-6 tracking-[-0.36px] text-black">특별히 두드러진 위험 요인이 없어요</p>
              <p className="mt-[10px] text-[14px] font-medium leading-5 tracking-[-0.28px] text-[#515151]">
                가장 큰 요인도 위험도를 1%p 미만으로 움직였어요.
              </p>
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
          <ul className="mt-5 flex flex-col gap-2 text-[13px] leading-[18px] text-[#b4b4b4]">
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
        <p className="mt-5 text-[12px] leading-[18px] text-[#9b9b9b]">{r.disclaimer}</p>
      </section>

      {/* 개선 방향 */}
      <section className="px-[30px] pt-[52px]">
        <h2 className="text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
          위험요인에 대해
          <br />
          개선 방향을 제안드릴게요
        </h2>
        {actions.length === 0 ? (
          <p className="mt-[26px] text-[14px] leading-5 text-[#9b9b9b]">
            지금 가게 데이터로 제안할 수 있는 개선 방향이 아직 없어요. 효과를 확인하는 분석이 끝나면 더 구체적인 방향을 알려드릴게요.
          </p>
        ) : (
          <Steps items={actions} />
        )}
      </section>

      <div className="mt-14 h-3 bg-[#232323]" />

      {/* 지원사업 */}
      <section className="pt-14">
        <h2 className="px-[30px] text-[22px] font-medium leading-[30px] tracking-[-0.44px]">
          받을 수 있는 지원에는
          <br />
          이런 것들이 있어요
        </h2>
        {r.policy_matching === "not_performed" ? (
          <p className="mt-[26px] px-[30px] text-[14px] leading-5 text-[#9b9b9b]">지원사업 정보를 준비하고 있어요.</p>
        ) : r.policies.length === 0 ? (
          <p className="mt-[26px] px-[30px] text-[14px] leading-5 text-[#9b9b9b]">지금 가게 조건에 맞는 지원사업을 찾지 못했어요.</p>
        ) : (
          <div className="no-scrollbar mt-[26px] flex snap-x gap-[5px] overflow-x-auto px-[30px]">
            {r.policies.map((p) => (
              <article key={p.id} className="flex min-h-[187px] w-[169px] shrink-0 snap-start flex-col justify-between gap-[22px] rounded-[10px] bg-[#f0f3fb] p-5">
                <div className="flex flex-col gap-[6px]">
                  <span aria-hidden className="flex size-10 items-center justify-center rounded-full bg-[#373e58] text-[15px] font-semibold text-[#f0f3fb]">
                    {p.operator.replace(/^\(예시\)\s*/, "").slice(0, 1)}
                  </span>
                  <p className="text-[16px] font-semibold leading-5 text-black">{p.name}</p>
                  {p.linked_factor_ids.length > 0 && (
                    <p className="text-[12px] font-medium leading-4 text-[#373e58]">{p.linked_factor_ids.map((id) => LABEL[id]).join(", ")} 관련</p>
                  )}
                  {p.match_status === "check_required" && (
                    <p className="text-[12px] font-medium leading-4 text-[#c72f2f]">조건 확인 필요: {p.unverifiable_conditions.join(", ")}</p>
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
            ))}
          </div>
        )}
      </section>

      <footer className="px-[33px] pt-[45px] text-[14px] font-semibold leading-5 text-muted">
        <p>
          {ymLabel(r.as_of)} 기준 진단이에요. 가게 자체 매출은 반영하지 않았고, 주변 상권·같은 업종 경쟁·온라인 후기 같은 공개 데이터로 만든 예측이에요.
        </p>
        <Link href="/" className="mt-6 inline-block underline underline-offset-2">
          다른 가게 검색하기
        </Link>
      </footer>
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-3">
      <dt className="shrink-0 font-medium tracking-[-0.3px] text-[#898989]">{k}</dt>
      <dd className="min-w-0 break-keep font-normal">{v}</dd>
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
      <p className={`text-[14px] font-semibold leading-5 tracking-[-0.28px] ${up ? "text-[#373e58]" : "text-[#418b59]"}`}>
        {f.pp} · {f.levelText}
      </p>
      <p className="mt-[2px] text-[18px] font-semibold leading-6 tracking-[-0.36px] text-black">
        {f.label}
        {f.ownerActionable && <span className="ml-2 inline-block rounded-full bg-[#373e58] px-2 py-[2px] align-middle text-[11px] font-semibold text-white">직접 관리할 수 있어요</span>}
      </p>
      <p className="mt-[10px] text-[14px] font-medium leading-5 tracking-[-0.28px] text-[#515151]">
        {f.sentence}
        {f.peerText && ` ${f.peerText}.`}
      </p>
    </div>
  );
}

const BADGE = {
  verified: { text: "효과 확인", border: "#418b59", bg: "#51594f" },
  unverified: { text: "효과 미확인", border: "#c72f2f", bg: "#5f4f4f" },
  pending: { text: "분석 준비 중", border: "#898989", bg: "#535353" },
} as const;

function Steps({ items }: { items: ActionItem[] }) {
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
              <span className="inline-flex h-[31px] items-center gap-[7px] self-start rounded-[20.5px] border px-3 text-[12px] font-semibold tracking-[-0.24px] text-white" style={{ borderColor: b.border, background: b.bg }}>
                <span aria-hidden className="size-[9px] rounded-full" style={{ background: b.border }} />
                {b.text}
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
