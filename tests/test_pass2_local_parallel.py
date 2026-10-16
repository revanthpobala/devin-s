import threading
from unittest.mock import patch

from src.logic.deep_research import pass2


def _run(conc):
    seen = set()

    def fake(*a, **k):
        seen.add(threading.current_thread().name)
        return "x"

    with patch.object(pass2, "_call_model", side_effect=fake), patch("src.config.LLM_LOCAL_CONCURRENCY", conc):
        r = pass2.run_pass2("T", "2026-10-15", "s", "u", "si", "ui", [], [], force_local=True)
    assert r.response == "x" and r.ind_response == "x"
    return seen


def test_local_runs_a_and_b_in_worker_threads_when_server_has_slots():
    assert all(n != threading.current_thread().name for n in _run(4))


def test_local_stays_sequential_with_one_slot():
    assert _run(1) == {threading.current_thread().name}
