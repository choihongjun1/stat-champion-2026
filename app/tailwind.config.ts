import type { Config } from "tailwindcss";

// 색상·글꼴 값은 피그마 '통체전' 파일의 검색 프레임에서 그대로 가져왔습니다.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0f1014", // 화면 배경
        field: "#22212f", // 입력창·구 목록 배경
        "field-active": "#4e4c68", // 선택된 구, 구·동 목록 구분선
        hint: "#797886", // 입력창 placeholder·아이콘
        muted: "#5e6780", // '검색결과' 라벨, 하단 링크, 결과 구분선
        sub: "#dadff1", // 결과 목록의 주소
        scroll: "#6f7585", // 동 목록 스크롤 표시
      },
      fontFamily: {
        sans: ['"Pretendard Variable"', "Pretendard", "-apple-system", "BlinkMacSystemFont", "system-ui", '"Apple SD Gothic Neo"', '"Noto Sans KR"', "sans-serif"],
      },
      maxWidth: { screen: "402px" },
    },
  },
  plugins: [],
};
export default config;
