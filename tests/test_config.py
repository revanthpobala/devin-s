from src import config


def test_base_dir_exists():
    assert config.BASE_DIR.exists()
    assert config.BASE_DIR.is_dir()
    assert (config.BASE_DIR / "src").exists()


def test_default_config_types():
    assert isinstance(config.POLLING_INTERVAL, int)
    assert isinstance(config.LLM_LOCAL_CONCURRENCY, int)
    assert isinstance(config.SENDER_EMAIL, str)


def test_get_python_exe():
    py_exe = config.get_python_exe()
    assert isinstance(py_exe, str)
    assert len(py_exe) > 0
