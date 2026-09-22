"""Opt-in real DeepSeek smoke test for the three Agent roles. Never runs by default."""

import os

import pytest

from scripts.verify_agent_llm_live import main


@pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_SMOKE") != "1" or not os.getenv("DEEPSEEK_API_KEY"),
    reason="需要 RUN_DEEPSEEK_SMOKE=1 和 DEEPSEEK_API_KEY",
)
def test_real_deepseek_roles_return_contract_valid_output(capsys) -> None:
    assert main() == 0
    output = capsys.readouterr().out
    assert "RESULT: all three roles produced contract-valid output" in output
    assert os.environ["DEEPSEEK_API_KEY"] not in output, "报告不得包含 API key"
