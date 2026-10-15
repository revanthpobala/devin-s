from src import _proc_hygiene as ph


class _FakePopen:
    seen = None

    def __init__(self, *args, **kwargs):
        type(self).seen = kwargs.get("creationflags")


def test_children_get_no_window_unless_caller_chose_flags():
    assert ph.install(_FakePopen, force=True) is True
    assert ph.install(_FakePopen, force=True) is False

    _FakePopen(["x"])
    assert _FakePopen.seen == ph.CREATE_NO_WINDOW

    _FakePopen(["x"], creationflags=0x200)
    assert _FakePopen.seen == 0x200 | ph.CREATE_NO_WINDOW

    _FakePopen(["x"], creationflags=ph.DETACHED_PROCESS)
    assert _FakePopen.seen == ph.DETACHED_PROCESS

    _FakePopen(["x"], creationflags=ph.CREATE_NEW_CONSOLE)
    assert _FakePopen.seen == ph.CREATE_NEW_CONSOLE
