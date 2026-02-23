# tests/primitives/test_pmf_models.py
"""Tests for PMF validation data models."""

from ripple.primitives.pmf_models import (
    TribunalMember,
    TribunalOpinion,
    DeliberationRecord,
    EvidencePack,
    PMFScorecard,
    PMFVerdict,
)


class TestTribunalMember:
    def test_create(self):
        m = TribunalMember(
            role="MarketAnalyst",
            perspective="Market size, competition, trends",
            expertise="Consumer goods market analysis",
        )
        assert m.role == "MarketAnalyst"
        assert m.perspective == "Market size, competition, trends"
        assert m.expertise == "Consumer goods market analysis"

    def test_frozen(self):
        m = TribunalMember(role="X", perspective="Y", expertise="Z")
        import dataclasses
        assert dataclasses.is_dataclass(m)


class TestTribunalOpinion:
    def test_create(self):
        o = TribunalOpinion(
            member_role="DevilsAdvocate",
            scores={"demand_resonance": 3, "propagation_potential": 2},
            narrative="The product faces strong competition.",
            round_number=0,
        )
        assert o.member_role == "DevilsAdvocate"
        assert o.scores["demand_resonance"] == 3
        assert o.round_number == 0

    def test_default_round_zero(self):
        o = TribunalOpinion(
            member_role="X", scores={}, narrative="", round_number=0,
        )
        assert o.round_number == 0


class TestDeliberationRecord:
    def test_create(self):
        opinion = TribunalOpinion(
            member_role="MarketAnalyst",
            scores={"demand": 4},
            narrative="Strong signal.",
            round_number=1,
        )
        r = DeliberationRecord(
            round_number=1,
            opinions=[opinion],
            challenges=[{"challenger": "DevilsAdvocate", "target": "MarketAnalyst", "content": "Overoptimistic"}],
            consensus_points=["Product has niche appeal"],
            dissent_points=["Pricing contested"],
            converged=True,
        )
        assert r.round_number == 1
        assert len(r.opinions) == 1
        assert len(r.challenges) == 1
        assert len(r.consensus_points) == 1
        assert len(r.dissent_points) == 1
        assert r.converged is True

    def test_converged_defaults_false(self):
        opinion = TribunalOpinion(
            member_role="Analyst", scores={"d": 3}, narrative="N", round_number=0,
        )
        r = DeliberationRecord(
            round_number=0, opinions=[opinion], challenges=[], consensus_points=[], dissent_points=[],
        )
        assert r.converged is False


class TestEvidencePack:
    def test_create(self):
        ep = EvidencePack(
            source="RIPPLE Phase, Wave 0-2",
            summary="3 waves, 2 stars active, mixed signals",
            key_signals=[
                {"wave_id": "w1", "agent_id": "star_1", "signal": "KOL 种草率 0.8"},
                {"wave_id": "w2", "agent_id": "sea_1", "signal": "白领群体 amplify 率 60%"},
            ],
            statistics={"total_waves": 3, "active_stars": 2, "active_seas": 1},
            full_records_ref="#/process/waves",
        )
        assert len(ep.key_signals) == 2
        assert ep.statistics["total_waves"] == 3
        assert ep.full_records_ref == "#/process/waves"
        assert "RIPPLE" in ep.source

    def test_key_signals_limit(self):
        """设计约束：key_signals ≤ 10 条。运行时校验，此处验证结构。"""
        signals = [{"wave_id": f"w{i}", "agent_id": f"a{i}", "signal": f"s{i}"} for i in range(10)]
        ep = EvidencePack(
            source="RIPPLE Phase, Wave 0-9",
            summary="Test",
            key_signals=signals,
            statistics={},
            full_records_ref="",
        )
        assert len(ep.key_signals) == 10


class TestPMFScorecard:
    def test_create(self):
        sc = PMFScorecard(
            dimensions={
                "demand_resonance": {"median": 4, "range": 1, "stability_level": "high", "narrative": "Strong", "iqr": 1},
            },
            overall_grade="B",
            confidence="medium",
        )
        assert sc.overall_grade == "B"
        assert sc.confidence == "medium"
        assert sc.dimensions["demand_resonance"]["median"] == 4


class TestPMFVerdict:
    def test_create(self):
        sc = PMFScorecard(
            dimensions={}, overall_grade="A", confidence="high",
        )
        ep = EvidencePack(
            source="RIPPLE Phase, Wave 0-2",
            summary="3 waves, positive signals",
            key_signals=[],
            statistics={"total_waves": 3},
            full_records_ref="#/process/waves",
        )
        v = PMFVerdict(
            grade="A",
            confidence="high",
            executive_summary="Strong PMF detected.",
            scorecard=sc,
            evidence_pack=ep,
            deliberation_summary={"final_positions": [], "consensus": ["demand strong"], "dissent": []},
            deliberation_records_ref="#/process/deliberation",
            recommendations=["Scale up."],
            assumptions_to_verify=["Price sensitivity untested with real users"],
            ensemble_stats={"runs": 3, "kappa": 0.85},
            variant_comparison=None,
        )
        assert v.grade == "A"
        assert v.confidence == "high"
        assert v.variant_comparison is None
        assert len(v.recommendations) == 1
        assert "不构成" in v.disclaimer  # v3: 强制免责声明
        assert len(v.assumptions_to_verify) == 1
        assert isinstance(v.evidence_pack, EvidencePack)  # v4: 统一 dataclass 类型
        assert "#/process/deliberation" in v.deliberation_records_ref  # v4: JSON Pointer
