import stat
from pathlib import Path

import pytest


@pytest.fixture
def fake_ripple_cli(tmp_path: Path) -> Path:
    stub = tmp_path / "ripple-cli"
    stub.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import os",
                "import sys",
                "",
                'print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}))',
                "",
            ]
        ),
        encoding="utf-8",
    )
    stub.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return stub
