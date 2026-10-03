/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 정적 HTML 내보내기는 시험용 스위치로만 켠다: STATIC_EXPORT=1 npm run build → out/
  // (웹 범위 결론 R5 전까지 기본값은 일반 빌드. out/은 로컬 HTTP 서버로 연다 — file:// 더블클릭은 동작하지 않음)
  ...(process.env.STATIC_EXPORT === "1" ? { output: "export", trailingSlash: true, images: { unoptimized: true } } : {}),
};
export default nextConfig;
