# ripple/api/ensemble.py
"""集成运行器 — PMF 验证的多次模拟与统计聚合。 / Ensemble runner — multiple simulation runs with statistical aggregation for PMF validation."""

import logging
import statistics
from typing import Any, Callable, Awaitable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def compute_median_iqr(values: List[float]) -> Tuple[float, float]:
    """计算中位数和四分位距。 / Compute median and interquartile range."""
    if not values:
        return 0.0, 0.0
    sorted_v = sorted(values)
    median = statistics.median(sorted_v)
    if len(sorted_v) < 2:
        return median, 0.0
    n = len(sorted_v)
    mid = n // 2
    if n % 2 == 0:
        q1 = statistics.median(sorted_v[:mid])
        q3 = statistics.median(sorted_v[mid:])
    else:
        # Inclusive quartiles: include median in both halves
        q1 = statistics.median(sorted_v[:mid + 1])
        q3 = statistics.median(sorted_v[mid:])
    return median, q3 - q1


def compute_fleiss_kappa(ratings_matrix: List[List[int]]) -> float:
    """计算 Fleiss' kappa 一致性系数。 / Compute Fleiss' kappa inter-rater agreement.

    Args:
        ratings_matrix: N items x K categories, each cell = count of raters selecting that category.

    Returns:
        Kappa coefficient (-1.0 to 1.0). 1.0 = perfect agreement, 0 = chance, <0 = below chance.
    """
    if not ratings_matrix or not ratings_matrix[0]:
        return 0.0

    n_items = len(ratings_matrix)
    n_categories = len(ratings_matrix[0])
    n_raters = sum(ratings_matrix[0])

    if n_raters <= 1 or n_items == 0:
        return 0.0

    # P_i for each item
    p_items = []
    for row in ratings_matrix:
        sum_sq = sum(r * r for r in row)
        p_i = (sum_sq - n_raters) / (n_raters * (n_raters - 1)) if n_raters > 1 else 0
        p_items.append(p_i)

    p_bar = sum(p_items) / n_items

    # P_e: expected agreement by chance
    p_j = []
    for j in range(n_categories):
        col_sum = sum(ratings_matrix[i][j] for i in range(n_items))
        p_j.append(col_sum / (n_items * n_raters))
    p_e = sum(pj * pj for pj in p_j)

    if p_e == 1.0:
        return 1.0

    kappa = (p_bar - p_e) / (1.0 - p_e)
    return kappa


def _kappa_to_consistency(kappa: float) -> str:
    """将 kappa 值转换为一致性等级。 / Convert kappa to consistency level."""
    if kappa >= 0.8:
        return "high"
    elif kappa >= 0.4:
        return "medium"
    else:
        return "low"


def aggregate_ordinal_scores(
    all_scores: List[Dict[str, int]],
) -> Dict[str, Dict[str, Any]]:
    """聚合多次运行的 ordinal 评分。 / Aggregate ordinal scores across runs."""
    if not all_scores:
        return {}

    dimensions = set()
    for s in all_scores:
        dimensions.update(s.keys())

    result: Dict[str, Dict[str, Any]] = {}
    for dim in sorted(dimensions):
        values = [float(s[dim]) for s in all_scores if dim in s]
        if not values:
            continue
        median, iqr = compute_median_iqr(values)
        disp_range = max(values) - min(values)
        # Build ratings matrix for this dimension (1-5 scale -> 5 categories)
        ratings_row = [0, 0, 0, 0, 0]
        for v in values:
            idx = max(0, min(4, int(v) - 1))
            ratings_row[idx] += 1
        # v4.1: 1-5 ordinal 的分散度主指标用 range(max-min)（离散可解释且实现一致）
        stability_level = "high" if disp_range <= 1 else ("medium" if disp_range <= 2 else "low")
        result[dim] = {
            "median": median,
            "range": disp_range,
            "stability_level": stability_level,
            "iqr": iqr,  # optional
            "values": values,
        }
    return result


class EnsembleRunner:
    """集成运行器：多次模拟 + 统计聚合。 / Ensemble runner: multiple runs + statistical aggregation."""

    def __init__(
        self,
        simulate_fn: Callable[..., Awaitable[Dict[str, Any]]],
        num_runs: int = 3,
    ):
        self._simulate_fn = simulate_fn
        self._num_runs = num_runs

    async def run(
        self,
        *,
        seeds: Optional[List[int]] = None,
        seed_key: str = "random_seed",
        **simulate_kwargs,
    ) -> List[Dict[str, Any]]:
        """运行 N 次模拟并返回所有结果。 / Run N simulations and return all results.

        注意：默认**串行执行**。PMF v4.1 要求单次 simulate() 共享同一个 BudgetState.max_calls，
        并且 Variant Isolation 依赖 seed 控制顺序随机化；并发会放大不确定性且不利于共享预算。
        """
        seeds_to_use: List[Optional[int]] = (
            list(seeds) if seeds is not None else [None] * self._num_runs
        )

        valid: List[Dict[str, Any]] = []
        error_count = 0
        for seed in seeds_to_use:
            kwargs = dict(simulate_kwargs)
            if seed is not None:
                kwargs[seed_key] = seed
            try:
                result = await self._simulate_fn(**kwargs)
                if isinstance(result, dict):
                    valid.append(result)
                else:
                    error_count += 1
            except Exception as exc:
                error_count += 1
                logger.warning("Ensemble run failed: %s", exc)

        if error_count:
            logger.warning(
                "Ensemble: %d of %d runs failed",
                error_count, len(seeds_to_use),
            )
        return valid
