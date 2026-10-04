import type { DongEntry, SearchEntry } from "./reportTypes";

const MAX_RESULTS = 30;
export const GU_ORDER = ["마포구", "영등포구", "광진구"]; // 피그마 '검색_실패시 주소' 순서

// src/data/names.py normalize_name 이식 — 지점명(…점)과 괄호 부가표기를 빼고 한글·영문·숫자만 남긴다
const BRANCH = /^[가-힣A-Za-z0-9·]+점$/;
const KEEP = /[^가-힣A-Za-z0-9]/g;
export function normalizeName(raw: string): string {
  let t = raw.normalize("NFKC").toUpperCase().replace(/\s+/g, " ").trim();
  t = t.replace(/\(([^()]*)\)/g, " ").replace(/\s+/g, " ").trim();
  const tokens = t.split(" ");
  if (tokens.length >= 2 && BRANCH.test(tokens[tokens.length - 1])) tokens.pop();
  const base = tokens.join(" ").replace(KEEP, "");
  return base || t.replace(KEEP, "");
}
const full = (s: string) => s.normalize("NFKC").toUpperCase().replace(/\([^()]*\)/g, "").replace(KEEP, "");

// 주소 정규화 (REPORT_SCHEMA §12) — 공백·문장부호 제거, 앞의 서울 표기 제거
function normAddr(s: string): string {
  return s.normalize("NFKC").toUpperCase().replace(/^(서울특별시|서울시|서울)\s*/, "").replace(/[^가-힣A-Z0-9\-]/g, "");
}

function isAreaToken(tok: string, dongs: DongEntry[]): DongEntry | "gu" | null {
  const t = tok.replace(/\s/g, "");
  const d = dongs.find((x) => x.dong === t || (t.length >= 2 && x.dong.replace(/(\d*동|\d*가)$/, "") === t && /동$|가$/.test(x.dong) && /동$/.test(t)));
  if (d) return d;
  if (["광진구", "마포구", "영등포구", "광진", "마포", "영등포"].includes(t)) return "gu";
  return null;
}

/**
 * 상호명(+동) 검색. 상호 일치 단계: 0 지점명까지 같음 → 1 상호 같음 → 2 앞부분 → 3 포함.
 * 동·구 이름이 섞여 있으면 그 지역으로 좁힌다. 상호로 못 찾으면 주소(도로명·지번)로 찾는다.
 */
export function searchStores(query: string, index: SearchEntry[], dongs: DongEntry[]): SearchEntry[] {
  const q = query.trim();
  if (!q) return [];
  const tokens = q.split(/\s+/);
  const areaDongs: DongEntry[] = [];
  const areaGus: string[] = [];
  const nameTokens: string[] = [];
  for (const t of tokens) {
    const a = /(동|가|구)$/.test(t) ? isAreaToken(t, dongs) : null;
    if (a === "gu") areaGus.push(t.endsWith("구") ? t : `${t}구`);
    else if (a) areaDongs.push(a);
    else nameTokens.push(t);
  }
  const nameQ = normalizeName(nameTokens.join(" "));
  const fullQ = full(nameTokens.join(" "));

  let hits: { e: SearchEntry; stage: number }[] = [];
  if (nameQ.length >= 2 || (nameQ.length >= 1 && areaDongs.length)) {
    for (const e of index) {
      if (!e.name || !e.name_norm) continue;
      const stage = full(e.name) === fullQ ? 0 : e.name_norm === nameQ ? 1 : e.name_norm.startsWith(nameQ) ? 2 : e.name_norm.includes(nameQ) ? 3 : -1;
      if (stage >= 0) hits.push({ e, stage });
    }
    const inArea = (e: SearchEntry) =>
      (areaDongs.length === 0 || areaDongs.some((d) => d.gu === e.gu && d.dong === e.dong)) && (areaGus.length === 0 || areaGus.includes(e.gu));
    const narrowed = hits.filter((h) => inArea(h.e));
    if (narrowed.length) hits = narrowed;
  }

  if (hits.length === 0) {
    const aq = normAddr(q);
    if (aq.length >= 2) {
      for (const e of index) {
        for (const a of [e.address_road, e.address_jibun]) {
          if (!a) continue;
          const na = normAddr(a);
          const i = na.indexOf(aq);
          if (i >= 0) {
            const nextIsDigit = /\d/.test(na[i + aq.length] ?? "");
            hits.push({ e, stage: nextIsDigit ? 5 : 4 });
            break;
          }
        }
      }
    }
  }

  return hits
    .sort(
      (a, b) =>
        a.stage - b.stage ||
        a.e.gu.localeCompare(b.e.gu, "ko") ||
        (a.e.dong ?? "힣").localeCompare(b.e.dong ?? "힣", "ko") ||
        (a.e.name_norm ?? "").localeCompare(b.e.name_norm ?? "", "ko") ||
        a.e.store_id.localeCompare(b.e.store_id),
    )
    .slice(0, MAX_RESULTS)
    .map((h) => h.e);
}

/** 구별 법정동 목록 (가나다순) */
export function dongsByGu(dongs: DongEntry[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const d of dongs) (out[d.gu] ??= []).push(d.dong);
  for (const g of Object.keys(out)) out[g].sort((a, b) => a.localeCompare(b, "ko"));
  return out;
}
