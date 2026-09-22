"""W1-4 시범 수집 대상 표본 추출.

gwangjin_targets.csv(광진구 인허가 전체)에서 업종 x 영업상태 비율을 유지한
층화표집으로 표본을 뽑는다. 재현성을 위해 seed를 고정한다.

사용법:
    python src/data/sample_gwangjin_targets.py --n 1000
"""

import argparse

import pandas as pd

SRC_PATH = "data/interim/gwangjin_targets.csv"
OUT_PATH = "data/interim/gwangjin_sample.csv"
SEED = 42


def main(n: int) -> None:
    df = pd.read_csv(SRC_PATH, dtype=str)
    frac = n / len(df)

    sample = df.groupby(["업종", "status"], group_keys=False).sample(
        frac=frac, random_state=SEED
    )

    # 반올림 오차로 n과 어긋나면 무작위로 맞춘다 (같은 seed 기준).
    if len(sample) > n:
        sample = sample.sample(n=n, random_state=SEED)
    elif len(sample) < n:
        remainder = df.drop(sample.index).sample(n=n - len(sample), random_state=SEED)
        sample = pd.concat([sample, remainder])

    sample = sample.sample(frac=1, random_state=SEED).reset_index(drop=True)
    sample.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"{len(sample)}건 -> {OUT_PATH}")
    print(sample.groupby(["업종", "status"]).size())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000)
    args = parser.parse_args()
    main(args.n)
