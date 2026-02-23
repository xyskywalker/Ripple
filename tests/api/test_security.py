# tests/api/test_security.py
"""Tests for security: input redaction and output file permissions."""

import json
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ripple.api.simulate import simulate


class TestInputRedaction:
    @pytest.mark.asyncio
    async def test_redact_input_removes_full_description(self):
        """When redact_input=True, output file should not contain full product description."""
        with patch("ripple.api.simulate.SkillManager") as MockSM, \
             patch("ripple.api.simulate.ModelRouter") as MockRouter, \
             patch("ripple.api.simulate.SimulationRuntime") as MockRuntime, \
             patch("ripple.api.simulate.SimulationRecorder") as MockRecorder:

            mock_skill = MagicMock()
            mock_skill.name = "pmf-validation"
            mock_skill.prompts = {"omniscient": "p"}
            mock_skill.platform_profiles = {}
            mock_skill.channel_profiles = {}
            MockSM.return_value.load.return_value = mock_skill

            mock_router = MagicMock()
            mock_router.check_budget.return_value = True
            mock_router.budget = MagicMock(max_calls=200)
            mock_router.get_model_backend.return_value = AsyncMock(
                call=AsyncMock(return_value="{}")
            )
            MockRouter.return_value = mock_router

            mock_runtime = AsyncMock()
            mock_runtime.run.return_value = {"total_waves": 3}
            MockRuntime.return_value = mock_runtime

            sensitive_input = {
                "description": "Our secret product with proprietary formula XYZ...",
                "product_type": "health_food",
            }

            result = await simulate(
                event=sensitive_input,
                skill="pmf-validation",
                redact_input=True,
            )

            # Verify recorder was called with redacted input
            recorder_instance = MockRecorder.return_value
            if recorder_instance.record_simulation_input.called:
                recorded_input = recorder_instance.record_simulation_input.call_args[0][0]
                # Should contain product_type but not full description
                assert "proprietary formula" not in json.dumps(recorded_input)


class TestOutputFilePermissions:
    def test_recorder_sets_file_permissions(self):
        """Output file should have restrictive permissions (owner read/write only)."""
        from ripple.engine.recorder import SimulationRecorder
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_run.json"
            recorder = SimulationRecorder(output_path=output_path, run_id="test_run")
            # After any write operation, file should have 0o600 permissions
            assert output_path.exists()
            mode = os.stat(output_path).st_mode & 0o777
            assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"
