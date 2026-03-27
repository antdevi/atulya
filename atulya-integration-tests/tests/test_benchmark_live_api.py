from __future__ import annotations

from pathlib import Path
import subprocess


def test_live_api_benchmark_smoke(tmp_path: Path):
    result = subprocess.run(
        [
            "uv",
            "run",
            "atulya-benchmark",
            "--mode",
            "live-api",
            "--scenario-ids",
            "temporal-01,portability-01",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Mode: live-api" in result.stdout
    assert (tmp_path / "leaderboard.live.json").exists()
    assert (tmp_path / "leaderboard.live.md").exists()
