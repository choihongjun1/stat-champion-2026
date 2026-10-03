// 정적 JSON 번들 읽기 (REPORT_SCHEMA §13). 진입 파일은 meta.json.
// 번들 위치는 NEXT_PUBLIC_BUNDLE_BASE로 바꿀 수 있다 (기본: public/bundle = 합성 샘플).
// ⚠️ 실제 점포 번들은 공개 승인(publication_approved) 전에는 public/에 넣거나 배포하지 않는다.
// 제출용 비식별 사례 번들(submission-static-0.1)은 meta.json의 _submission_contract_version으로 구분한다 (scripts/export-submission.mjs).

import type { BundleMeta, CaseLabel, DongEntry, DongSummaryRow, Report, SearchEntry, SubmissionCase, SubmissionMeta } from "./reportTypes";

const BASE = (process.env.NEXT_PUBLIC_BUNDLE_BASE || "/bundle").replace(/\/$/, "");

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}/${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json() as Promise<T>;
}

let metaP: Promise<BundleMeta | SubmissionMeta> | null = null;
const loadAnyMeta = () => (metaP ??= getJson<BundleMeta | SubmissionMeta>("meta.json"));

export const isSubmissionMeta = (m: BundleMeta | SubmissionMeta): m is SubmissionMeta => "_submission_contract_version" in m;

/** 전체 점포 번들(검색·리포트)의 meta. 제출용 사례 번들이면 거부한다 */
export async function loadMeta(): Promise<BundleMeta> {
  const m = await loadAnyMeta();
  if (isSubmissionMeta(m)) throw new Error("submission bundle has no search index");
  return m;
}

/** 지금 번들이 제출용 사례 번들이면 그 meta, 아니면 null */
export async function loadSubmissionMeta(): Promise<SubmissionMeta | null> {
  const m = await loadAnyMeta();
  return isSubmissionMeta(m) ? m : null;
}

/** 선택되지 않은 칸이거나 제출용 번들이 아니면 null */
export async function loadCase(label: CaseLabel): Promise<{ c: SubmissionCase; meta: SubmissionMeta } | null> {
  const meta = await loadSubmissionMeta();
  const slot = meta?.cases.find((s) => s.case_label === label);
  if (!meta || !slot || slot.selection_status !== "selected" || !slot.path) return null;
  const c = await getJson<SubmissionCase>(slot.path);
  if (c.case_label !== label) throw new Error(`case ${label}: label mismatch`);
  return { c, meta };
}

export async function loadSearchIndex(): Promise<SearchEntry[]> {
  const m = await loadMeta();
  const f = await getJson<{ entries: SearchEntry[] }>(m.files.search_index);
  return f.entries;
}

export async function loadDongs(): Promise<DongEntry[]> {
  const m = await loadMeta();
  const f = await getJson<{ dongs: DongEntry[] }>(m.files.dongs);
  return f.dongs;
}

export async function loadDongSummary(): Promise<DongSummaryRow[]> {
  const m = await loadMeta();
  const f = await getJson<{ rows: DongSummaryRow[] }>(m.files.dong_summary);
  return f.rows;
}

/** 없는 점포면 null */
export async function loadReport(storeId: string): Promise<Report | null> {
  const m = await loadMeta();
  const path = m.report_path_template.replace("{store_id}", encodeURIComponent(storeId));
  const res = await fetch(`${BASE}/${path}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`report ${storeId}: ${res.status}`);
  return res.json() as Promise<Report>;
}
