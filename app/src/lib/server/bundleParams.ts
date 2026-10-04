// 정적 export용 경로 목록 (빌드할 때만 실행). 번들 폴더의 search_index·dongs에서 읽는다.
// 번들 위치는 NEXT_PUBLIC_BUNDLE_BASE (기본 /bundle → public/bundle).
import { readFileSync } from "node:fs";
import path from "node:path";

function bundleDir(): string {
  const base = (process.env.NEXT_PUBLIC_BUNDLE_BASE || "/bundle").replace(/^\/|\/$/g, "");
  return path.join(process.cwd(), "public", base);
}

function readJson<T>(file: string): T {
  return JSON.parse(readFileSync(path.join(bundleDir(), file), "utf8")) as T;
}

// 제출용 비식별 사례 번들(submission-static-0.1)에는 검색 색인·동 목록이 없다.
// output: export는 빈 경로 목록을 거부하므로 자리표시 경로 하나만 만들고, scripts/export-submission.mjs가 out/에서 지운다.
const isSubmission = () => "_submission_contract_version" in readJson<object>("meta.json");
const SUBMISSION_PLACEHOLDER = "_";

export function storeIdParams(): { storeId: string }[] {
  if (isSubmission()) return [{ storeId: SUBMISSION_PLACEHOLDER }];
  const meta = readJson<{ files: { search_index: string } }>("meta.json");
  const idx = readJson<{ entries: { store_id: string }[] }>(meta.files.search_index); // claims-allow: ID-01
  return idx.entries.map((e) => ({ storeId: e.store_id }));
}

export function dongParams(): { gu: string; dong: string }[] {
  if (isSubmission()) return [{ gu: SUBMISSION_PLACEHOLDER, dong: SUBMISSION_PLACEHOLDER }];
  const meta = readJson<{ files: { dongs: string } }>("meta.json");
  const f = readJson<{ dongs: { gu: string; dong: string }[] }>(meta.files.dongs);
  return f.dongs.map((d) => ({ gu: d.gu, dong: d.dong }));
}

/** 사례 칸 A/B/C는 submission-static-0.1 계약에 고정돼 있다 (선택 결과가 아니라 칸 이름) */
export function caseParams(): { caseId: string }[] {
  return ["A", "B", "C"].map((l) => ({ caseId: `CASE-${l}` }));
}
