"""W2-1 축A(collect_online_presence.py) 잔여 수집 대상 추출.

기존 online_presence_all.csv(100,879건)는 이번 리뷰 반영(구 체크, n_candidates,
ambiguous, error_type 등) 이전 스키마로 수집된 뒤 새 FIELDNAMES에 맞춰 마이그레이션한
파일이다. 여기에 --resume으로 잔여분을 이어 붙이면, 마이그레이션이 놓친 케이스나
스크립트 버전 차이로 헤더/컬럼 수가 어긋나 CSV가 깨질 위험이 있다 — 이 위험을 아예
피하기 위해 잔여분은 별도 입력·출력 파일로 완전히 분리해서 수집한다.

all_targets.csv(정본 모집단, 110,347건) 중 online_presence_all.csv에서 이미
성공(error 없음) 처리된 store_id를 제외한 행만 뽑는다.

사용법:
    python src/data/build_remaining_targets.py
"""

import pandas as pd

TARGETS_PATH = "data/interim/all_targets.csv"
DONE_PATH = "data/interim/online_presence_all.csv"
OUT_PATH = "data/interim/all_targets_remaining.csv"


def main() -> None:
    targets = pd.read_csv(TARGETS_PATH, dtype=str)
    done = pd.read_csv(DONE_PATH, dtype=str, keep_default_na=False)
    done_ids = set(done.loc[done["error"] == "", "store_id"])

    remaining = targets[~targets["store_id"].isin(done_ids)]
    remaining.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"정본 모집단 {len(targets)}건 중 기존 완료 {len(done_ids)}건 제외 -> {len(remaining)}건")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
