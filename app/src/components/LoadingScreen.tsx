// 피그마 리포트_loading(106:6287): 가운데 '대시가 진단중이에요...' + 80×80 로고 영상
export default function LoadingScreen() {
  return (
    <div role="status" aria-live="polite" className="fixed inset-0 flex flex-col items-center justify-center bg-canvas">
      <p className="text-center text-[20px] font-medium leading-[30px] tracking-[-0.4px] text-white">대시가 진단중이에요...</p>
      <video className="mt-[5px] size-20" autoPlay muted loop playsInline aria-hidden>
        <source src="/onboarding-loader.webm" type="video/webm" />
        <source src="/onboarding-loader.mp4" type="video/mp4" />
      </video>
    </div>
  );
}
