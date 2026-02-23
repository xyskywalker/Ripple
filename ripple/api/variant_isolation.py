"""多方案对比的变量控制协议。 / Variant Isolation Protocol for multi-variant comparison.

设计原则 / Design principles:
- Agent config 锁定：一次 INIT，所有 variant 共享 / Single INIT, shared across variants
- Random seed 传递：同一 run_idx 跨 variant 使用相同 seed / Same seed for same run_idx across variants
- Evaluation order shuffling：DELIBERATE 呈现顺序随机化 / Randomized variant order for tribunal

v4 明确：seed 作用域 / v4 clarification: seed scope:
- seed 用于呈现顺序随机化（Tribunal variant 排列）和内部洗牌（Agent 激活顺序）
- seed 可作为 prompt hint 传递给 OmniscientAgent 促进行为多样性
- seed 不能使 LLM 输出严格可重复（LLM API 不保证确定性）
- 用户不应期望"相同 seed = 完全相同结果"
"""

import random
from typing import List


def compute_variant_seeds(variant_name: str, base_seed: int, ensemble_runs: int) -> List[int]:
    """计算 variant 的 ensemble seeds。 / Compute per-run seeds for a variant.

    同一 run_idx 跨不同 variant 使用相同 seed（控制变量）。
    variant_name 不参与 seed 计算 — seed 只由 base_seed + run_idx 决定。
    / Same run_idx across variants gets same seed. variant_name does not affect seed.
    """
    return [base_seed + i for i in range(ensemble_runs)]


def shuffle_variant_order(variants: List[str], seed: int) -> List[str]:
    """随机化 variant 呈现顺序（避免位置偏差）。 / Randomize variant presentation order."""
    rng = random.Random(seed)
    shuffled = list(variants)
    rng.shuffle(shuffled)
    return shuffled
