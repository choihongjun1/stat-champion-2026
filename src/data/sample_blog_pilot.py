"""W2-1 축B(블로그 월별) 선수집 표본 추출.

all_targets.csv(3구 전체, 110,347건)에서 구 x 영업상태(영업/폐업) 비율을 유지한
층화표집으로 표본을 뽑는다. collect_blog_monthly.py가 priority 순으로 정렬해서
앞에서부터 받기 때문에, all_targets.csv를 그대로 --limit으로 잘라 쓰면 1순위
(영업중+최근 폐업)만 뽑혀 절단 비율이 과대추정된다 — 이 스크립트로 만든 표본을
입력으로 쓰면 그 편향을 피할 수 있다. 재현성을 위해 seed를 고정한다.

MAX_PAGES(2 vs 3~4)를 바꾸면 표본도 새 설정으로 다시 받아야 한 데이터에
페이지 기준이 섞이지 않는다 — 이 스크립트를 다시 돌려도 seed가 같아 동일한
300건이 나오므로, 표본 자체를 새로 뽑을 필요 없이 collect_blog_monthly.py만
재실행하면 된다.

사용법:
    python src/data/sample_blog_pilot.py --n 300
"""

import argparse

import pandas as pd

SRC_PATH = "data/interim/all_targets.csv"
OUT_PATH = "data/interim/blog_pilot_sample.csv"
SEED = 42


def main(n: int) -> None:
    df = pd.read_csv(SRC_PATH, dtype=str)
    df["is_active"] = df["status"] == "영업/정상"
    frac = n / len(df)

    sample = df.groupby(["구", "is_active"], group_keys=False).sample(
        frac=frac, random_state=SEED
    )

    # 반올림 오차로 n과 어긋나면 무작위로 맞춘다 (같은 seed 기준).
    if len(sample) > n:
        sample = sample.sample(n=n, random_state=SEED)
    elif len(sample) < n:
        remainder = df.drop(sample.index).sample(n=n - len(sample), random_state=SEED)
        sample = pd.concat([sample, remainder])

    sample = sample.sample(frac=1, random_state=SEED).reset_index(drop=True)
    sample = sample.drop(columns=["is_active", "priority"])
    sample.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"{len(sample)}건 -> {OUT_PATH}")
    print(sample.groupby(["구", "status"]).size())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    args = parser.parse_args()
    main(args.n)
