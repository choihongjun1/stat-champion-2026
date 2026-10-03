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

export function storeIdParams(): { storeId: string }[] {
  const meta = readJson<{ files: { search_index: string } }>("meta.json");
  const idx = readJson<{ entries: { store_id: string }[] }>(meta.files.search_index); // claims-allow: ID-01
  return idx.entries.map((e) => ({ storeId: e.store_id }));
}

export function dongParams(): { gu: string; dong: string }[] {
  const meta = readJson<{ files: { dongs: string } }>("meta.json");
  const f = readJson<{ dongs: { gu: string; dong: string }[] }>(meta.files.dongs);
  return f.dongs.map((d) => ({ gu: d.gu, dong: d.dong }));
}
