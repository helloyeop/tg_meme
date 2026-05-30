import pytest

from signer.live_swap_qa import run_live_swap_qa


def test_live_swap_qa_refuses_to_run_without_explicit_confirmation() -> None:
    with pytest.raises(RuntimeError, match="--confirm-live-swap"):
        run_live_swap_qa(confirm=False)
