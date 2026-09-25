# -*- coding: utf-8 -*-
"""시간 분할 검증 — random split 금지.

핵심 제약 2가지
---------------
1. **시간 순서**: 과거 origin으로 학습하고 이후 origin으로 검증한다.
   같은 점포가 18개 origin에 반복 등장하므로 random split은 동일 점포의
   과거·미래를 train/test에 동시에 넣어 성능을 과대평가한다.

2. **라벨 성숙 갭(embargo)**: `event_12m`은 origin_end 이후 12개월을 봐야
   확정된다. origin s의 라벨은 origin_end(s) + 12개월에야 알 수 있다.
   따라서 origin t를 예측하는 시점에 쓸 수 있는 학습 라벨은

       origin_end(s) + 12개월 <= origin_end(t)   →   s <= t - 4분기

   뿐이다. 갭 없이 t-1 분기까지 학습에 쓰면 **실제로는 아직 모르는 라벨을
   쓴 것**이 되어 시간 누수다. 기본 embargo는 4분기(=12개월).

구현상 fold는 train과 test 사이에 embargo 분기를 통째로 비운다 (train ≤ t−5). 수식상 경계(t−4)보다
한 분기 보수적인데, 폐업 신고 지연(성숙 컷오프 1개월)을 흡수하기 위해서다.

`LABEL_HORIZON_QUARTERS`를 바꾸면 다른 예측 구간에도 그대로 쓸 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LABEL_HORIZON_QUARTERS = 4  # event_12m = 12개월 = 4분기


@dataclass(frozen=True)
class Fold:
    """하나의 시간 분할 fold."""

    name: str
    train_origins: tuple[str, ...]
    test_origins: tuple[str, ...]
    embargo_origins: tuple[str, ...]  # 라벨 미성숙으로 버린 구간

    def masks(self, df: pd.DataFrame, origin_col: str = "origin"):
        tr = df[origin_col].isin(self.train_origins).to_numpy()
        te = df[origin_col].isin(self.test_origins).to_numpy()
        return tr, te

    def describe(self) -> dict:
        return {
            "fold": self.name,
            "train_origins": f"{self.train_origins[0]}~{self.train_origins[-1]}",
            "n_train_origins": len(self.train_origins),
            "embargo": f"{self.embargo_origins[0]}~{self.embargo_origins[-1]}"
            if self.embargo_origins
            else "-",
            "test_origins": f"{self.test_origins[0]}~{self.test_origins[-1]}",
        }


def sorted_origins(df: pd.DataFrame, origin_col: str = "origin") -> list[str]:
    """origin을 시간 순으로 정렬해 반환한다 (2021Q1 < 2021Q2 < ...)."""
    return sorted(df[origin_col].dropna().unique().tolist())


def rolling_origin_folds(
    df: pd.DataFrame,
    *,
    origin_col: str = "origin",
    min_train_origins: int = 4,
    embargo: int = LABEL_HORIZON_QUARTERS,
    test_size: int = 1,
) -> list[Fold]:
    """확장 윈도우(expanding window) 시간 분할.

    fold k:  train = origins[:i]  |  embargo = origins[i:i+embargo]  |  test = origins[i+embargo : ...]

    Parameters
    ----------
    min_train_origins : 첫 fold의 최소 학습 origin 수
    embargo : 라벨 성숙 대기 분기 수. 0으로 두면 누수가 생긴다(비교 실험용으로만).
    test_size : fold당 검증 origin 수
    """
    origins = sorted_origins(df, origin_col)
    folds: list[Fold] = []
    i = min_train_origins
    while i + embargo + test_size <= len(origins):
        folds.append(
            Fold(
                name=f"fold{len(folds) + 1}",
                train_origins=tuple(origins[:i]),
                embargo_origins=tuple(origins[i : i + embargo]),
                test_origins=tuple(origins[i + embargo : i + embargo + test_size]),
            )
        )
        i += test_size
    return folds


def final_holdout(
    df: pd.DataFrame,
    *,
    origin_col: str = "origin",
    embargo: int = LABEL_HORIZON_QUARTERS,
    test_size: int = 2,
    calib_size: int = 2,
) -> tuple[Fold, tuple[str, ...]]:
    """최종 보고용 단일 분할 + 보정(calibration) 전용 구간 분리.

    학습 | 보정 | embargo | 검증  순으로 자른다.
    보정 구간은 **학습에 쓰지 않은** 데이터여야 isotonic/Venn-ABERS가 정직하다.
    """
    origins = sorted_origins(df, origin_col)
    need = calib_size + embargo + test_size
    if len(origins) <= need:
        raise ValueError(f"origin이 부족하다: {len(origins)} <= {need}")

    test = tuple(origins[-test_size:])
    emb = tuple(origins[-(test_size + embargo) : -test_size]) if embargo else ()
    calib = tuple(origins[-(test_size + embargo + calib_size) : -(test_size + embargo)])
    train = tuple(origins[: -(test_size + embargo + calib_size)])

    fold = Fold(name="final", train_origins=train, test_origins=test, embargo_origins=emb)
    return fold, calib


def random_split_baseline(
    df: pd.DataFrame, *, test_frac: float = 0.2, seed: int = 20260922
):
    """비교 전용 무작위 분할. **운영 모형에 쓰지 않는다.**

    시간 분할과의 성능 격차를 보고서에 싣기 위한 대조군이다
    (`W2_DETECT_DIAGNOSE.md` §6 — "random split 대비 성능 차이를 함께 싣는다").
    """
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(df))
    n_test = int(len(df) * test_frac)
    te = np.zeros(len(df), dtype=bool)
    te[idx[:n_test]] = True
    return ~te, te


def store_holdout_split(
    df: pd.DataFrame,
    *,
    store_col: str = "store_id",
    test_frac: float = 0.2,
    seed: int = 20260922,
):
    """점포 단위 홀드아웃 — 민감도 분석용.

    시간 분할이 주 검증이고 이건 보조다. "처음 보는 점포"에 대한 일반화를 본다.
    """
    rng = np.random.default_rng(seed)
    stores = df[store_col].unique()
    pick = rng.permutation(len(stores))[: int(len(stores) * test_frac)]
    test_stores = set(stores[pick])
    te = df[store_col].isin(test_stores).to_numpy()
    return ~te, te
