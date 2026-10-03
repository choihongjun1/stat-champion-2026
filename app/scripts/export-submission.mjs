// 제출용 비식별 사례 번들(submission-static-0.1)로 정적 HTML을 만든다 (README '정적 HTML 시험'과 같은 STATIC_EXPORT=1 빌드 → out/).
//
//   node scripts/export-submission.mjs [--src ../outputs/w3_submission/final]
//
// 1) 원본 번들을 읽기만 한다: 계약 버전·manifest(sha256 앞 12자·바이트)·파일 목록이 맞지 않으면 멈춘다. 원본은 바꾸지 않는다.
// 2) public/submission/(git 무시)에 복사 → NEXT_PUBLIC_BUNDLE_BASE=/submission 으로 정적 빌드 → 복사본은 지운다.
// 3) out/에서 제출 범위 밖 경로(합성 W2 번들, 점포 리포트·동 화면 자리표시)를 지운다. out/submission이 원본과 바이트 단위로 같은지 확인한다.
// private 경로(outputs/w3_demo/private 등)는 입력으로 받지 않는다. publication_approved는 읽지도 바꾸지도 않는다.

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, rmdirSync, statSync, unlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CONTRACT = "submission-static-0.1";
const BASE = "submission";
const OUT_REMOVE = ["bundle", "bundle_no_policy", "report", "dong"]; // 제출 범위 밖 (R5)

const argSrc = process.argv.indexOf("--src");
const src = path.resolve(APP, argSrc > 0 ? process.argv[argSrc + 1] : "../outputs/w3_submission/final");
const pub = path.join(APP, "public", BASE);
const out = path.join(APP, "out");

const fail = (msg) => {
  console.error(`export-submission: ${msg}`);
  process.exit(1);
};
const sha = (p) => createHash("sha256").update(readFileSync(p)).digest("hex");
const walk = (dir) =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]));
// fs.rmSync·cpSync의 recursive 처리는 Node 24 Windows에서 한글 경로에 대해 비정상 종료한다 — 직접 지우고 복사한다
const rmrf = (p) => {
  if (!existsSync(p)) return;
  if (lstatSync(p).isDirectory()) {
    for (const e of readdirSync(p)) rmrf(path.join(p, e));
    rmdirSync(p);
  } else unlinkSync(p);
};
const copyDir = (from, to) => {
  mkdirSync(to, { recursive: true });
  for (const e of readdirSync(from, { withFileTypes: true })) {
    if (e.isDirectory()) copyDir(path.join(from, e.name), path.join(to, e.name));
    else copyFileSync(path.join(from, e.name), path.join(to, e.name));
  }
};
const rel = (root, p) => path.relative(root, p).split(path.sep).join("/");

// ---- 1) 원본 검사 (읽기만) ----
if (src.split(path.sep).some((part) => part.toLowerCase() === "private")) fail(`private 경로는 입력으로 쓰지 않습니다: ${src}`);
if (!existsSync(path.join(src, "meta.json"))) fail(`meta.json 없음: ${src}`);
const meta = JSON.parse(readFileSync(path.join(src, "meta.json"), "utf8"));
const manifest = JSON.parse(readFileSync(path.join(src, "manifest.json"), "utf8"));
if (meta._submission_contract_version !== CONTRACT || manifest._submission_contract_version !== CONTRACT) fail(`계약 버전이 ${CONTRACT}가 아님`);
const listed = new Set(manifest.files.map((f) => f.path));
const actual = walk(src).map((p) => rel(src, p)).filter((p) => p !== "manifest.json");
for (const p of actual) if (!listed.has(p)) fail(`manifest에 없는 파일: ${p}`);
for (const f of manifest.files) {
  const p = path.join(src, f.path);
  if (!existsSync(p)) fail(`manifest 파일 없음: ${f.path}`);
  if (sha(p).slice(0, 12) !== f.sha256_12 || statSync(p).size !== f.bytes) fail(`manifest 불일치: ${f.path}`);
}
const before = Object.fromEntries(walk(src).map((p) => [rel(src, p), sha(p)]));

// ---- 2) 복사 → 빌드 → 복사본 삭제 ----
rmrf(pub);
rmrf(out);
let code = 1;
try {
  copyDir(src, pub);
  const next = path.join(APP, "node_modules", "next", "dist", "bin", "next");
  const r = spawnSync(process.execPath, [next, "build"], {
    cwd: APP,
    stdio: "inherit",
    env: { ...process.env, STATIC_EXPORT: "1", NEXT_PUBLIC_BUNDLE_BASE: `/${BASE}` },
  });
  code = r.status ?? 1;
} finally {
  rmrf(pub);
}
if (code !== 0) fail(`next build 실패 (exit ${code})`);

// ---- 3) 제출 범위 밖 경로 정리와 확인 ----
for (const d of OUT_REMOVE) rmrf(path.join(out, d));
const outBundle = path.join(out, BASE);
const exported = Object.fromEntries(walk(outBundle).map((p) => [rel(outBundle, p), sha(p)]));
if (JSON.stringify(Object.keys(exported).sort()) !== JSON.stringify(Object.keys(before).sort())) fail("out/submission 파일 목록이 원본과 다름");
for (const [k, v] of Object.entries(before)) if (exported[k] !== v) fail(`out/submission 바이트 불일치: ${k}`);
for (const [k, v] of Object.entries(before)) if (sha(path.join(src, k)) !== v) fail(`원본이 바뀜: ${k}`);

console.log(`export-submission: out/ 생성 완료 — ${CONTRACT}, 사례 ${meta.cases.filter((c) => c.selection_status === "selected").length}건, 원본 바이트 불변`);
