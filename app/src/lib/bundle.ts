// 정적 JSON 번들 읽기 (REPORT_SCHEMA §13). 진입 파일은 meta.json.
// 번들 위치는 NEXT_PUBLIC_BUNDLE_BASE로 바꿀 수 있다 (기본: public/bundle = 합성 샘플).
// ⚠️ 실제 점포 번들은 공개 승인(publication_approved) 전에는 public/에 넣거나 배포하지 않는다.

import type { BundleMeta, DongEntry, DongSummaryRow, Report, SearchEntry } from "./reportTypes";

const BASE = (process.env.NEXT_PUBLIC_BUNDLE_BASE || "/bundle").replace(/\/$/, "");

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}/${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json() as Promise<T>;
}

let metaP: Promise<BundleMeta> | null = null;
export const loadMeta = () => (metaP ??= getJson<BundleMeta>("meta.json"));

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
