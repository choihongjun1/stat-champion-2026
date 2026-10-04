import { LogoLarge } from "./Logo";

// 피그마 온보딩(26:748): 가운데 큰 로고 + 아래 80×80 로딩 애니메이션.
// 애니메이션은 디자이너 원본 영상을 배경색(#0f1014)에 맞추고 240px로 줄인 것입니다.
export default function Splash({ leaving }: { leaving: boolean }) {
  return (
    <div
      aria-hidden={leaving}
      className={`fixed inset-0 z-10 flex flex-col items-center justify-center bg-canvas transition-opacity duration-300 ${
        leaving ? "pointer-events-none opacity-0" : "opacity-100"
      }`}
    >
      <LogoLarge />
      <video className="mt-[29px] size-20" autoPlay muted loop playsInline aria-label="불러오는 중">
        {/* WebM(VP9)을 먼저, 못 읽는 브라우저(구형 사파리)는 MP4(H.264) */}
        <source src="/onboarding-loader.webm" type="video/webm" />
        <source src="/onboarding-loader.mp4" type="video/mp4" />
      </video>
    </div>
  );
}
