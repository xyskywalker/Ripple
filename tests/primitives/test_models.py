# tests/primitives/test_models.py
# 原语模型测试 / Primitive model tests

"""原语模型测试。 / Primitive model tests."""
import pytest
from ripple.primitives.models import (
    Ripple,
    Event,
    Meme,
    PhaseVector,
    Snapshot,
    Field,
    SimulationConfig,
    BudgetState,
    OmniscientVerdict,
    WaveRecord,
    AgentActivation,
)


class TestRipple:
    """Ripple 12 字段测试。 / Ripple 12-field tests."""

    def test_create_seed_ripple(self):
        """种子 Ripple: root_id = self.id。 / Seed Ripple: root_id = self.id."""
        r = Ripple(
            id="r1",
            content="test",
            content_embedding=[0.1, 0.2],
            energy=0.8,
            origin_agent="star1",
            ripple_type="original_post",
            emotion={"anger": 0.5},
            trace=["star1"],
            tick_born=1,
            mutations=[],
            root_id="r1",
        )
        assert r.root_id == r.id
        assert r.parent_id is None

    def test_create_child_ripple(self):
        """子 Ripple: root_id = parent.root_id。 / Child Ripple: root_id = parent.root_id."""
        child = Ripple(
            id="r2",
            content="reply",
            content_embedding=[0.3, 0.4],
            energy=0.5,
            origin_agent="sea1",
            ripple_type="comment",
            emotion={},
            trace=["star1", "sea1"],
            tick_born=1,
            mutations=[],
            parent_id="r1",
            root_id="r1",
        )
        assert child.root_id == "r1"
        assert child.parent_id == "r1"

    def test_root_id_not_empty(self):
        """root_id 默认值为空字符串（创建时必须赋值）。 / root_id defaults to empty string."""
        r = Ripple(
            id="r3",
            content="",
            content_embedding=[],
            energy=0,
            origin_agent="",
            ripple_type="",
            emotion={},
            trace=[],
            tick_born=0,
            mutations=[],
        )
        assert r.root_id == ""  # 默认值，实际使用时必须赋值 / Default; must be set in practice


class TestEvent:
    """Event 11 字段测试。 / Event 11-field tests."""

    def test_event_creation(self):
        e = Event(
            agent_id="sea1",
            action="comment",
            ripple_id="r1",
            tick=1,
            response_type="amplify",
            energy=0.8,
            effective_energy=0.6,
        )
        assert e.wave_index == 0
        assert e.drift_direction is None


class TestMeme:
    def test_meme_creation(self):
        m = Meme(tag="#test", heat=0.8, born_tick=1, last_referenced=1)
        assert m.heat == 0.8


class TestSimulationConfig:
    def test_defaults(self):
        config = SimulationConfig()
        assert config.max_waves == 8
        assert config.quiescent_wave_limit == 3


class TestOmniscientVerdict:
    def test_create_verdict_continue(self):
        activation = AgentActivation(
            agent_id="sea_young_women",
            incoming_ripple_energy=0.72,
            activation_reason="该群体对美妆内容高度敏感",
        )
        verdict = OmniscientVerdict(
            wave_number=3,
            simulated_time_elapsed="6h",
            simulated_time_remaining="42h",
            continue_propagation=True,
            activated_agents=[activation],
            skipped_agents=[],
            global_observation="内容在年轻女性群体中引发共鸣",
        )
        assert verdict.continue_propagation is True
        assert len(verdict.activated_agents) == 1
        assert verdict.activated_agents[0].agent_id == "sea_young_women"

    def test_create_verdict_terminate(self):
        verdict = OmniscientVerdict(
            wave_number=10,
            simulated_time_elapsed="20h",
            simulated_time_remaining="0h",
            continue_propagation=False,
            termination_reason="时间窗口耗尽",
            activated_agents=[],
            skipped_agents=[],
            global_observation="传播已趋于平静",
        )
        assert verdict.continue_propagation is False
        assert verdict.termination_reason == "时间窗口耗尽"

    def test_activated_agent_ids_property(self):
        activations = [
            AgentActivation(agent_id="sea_a", incoming_ripple_energy=0.5,
                            activation_reason="test"),
            AgentActivation(agent_id="star_b", incoming_ripple_energy=0.8,
                            activation_reason="test"),
        ]
        verdict = OmniscientVerdict(
            wave_number=0, simulated_time_elapsed="0h",
            simulated_time_remaining="48h", continue_propagation=True,
            activated_agents=activations, skipped_agents=[],
            global_observation="",
        )
        assert verdict.activated_agent_ids == ["sea_a", "star_b"]


class TestWaveRecord:
    def test_create_wave_record(self):
        verdict = OmniscientVerdict(
            wave_number=1, simulated_time_elapsed="2h",
            simulated_time_remaining="46h", continue_propagation=True,
            activated_agents=[], skipped_agents=[],
            global_observation="",
        )
        record = WaveRecord(
            wave_number=1,
            verdict=verdict,
            agent_responses={"sea_a": {"response_type": "amplify",
                                        "outgoing_energy": 0.6}},
            events=[],
        )
        assert record.wave_number == 1
        assert "sea_a" in record.agent_responses
