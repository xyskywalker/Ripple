from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_service_example_help_works_without_local_ripple_source(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    examples_src = repo / "examples"
    examples_dst = tmp_path / "examples"
    examples_dst.mkdir()

    for name in (
        "e2e_helpers.py",
        "e2e_xiaohongshu_common.py",
        "e2e_simulation_xiaohongshu_service.py",
    ):
        (examples_dst / name).write_text(
            (examples_src / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    proc = subprocess.run(
        [sys.executable, str(examples_dst / "e2e_simulation_xiaohongshu_service.py"), "--help"],
        cwd=examples_dst,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "HTTP+SSE 服务版" in proc.stdout
    assert "--artifacts-dir" not in proc.stdout
