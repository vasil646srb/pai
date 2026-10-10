from pai.settings import Settings, llm_config_from_env


def test_defaults_when_env_is_empty():
    s = Settings.from_env({})
    assert s == Settings()


def test_flags_are_on_unless_zero():
    s = Settings.from_env({"PAI_VERIFY": "0", "PAI_SERVICES": "0"})
    assert s.verify_enabled is False and s.services_enabled is False
    s = Settings.from_env({"PAI_VERIFY": "yes"})
    assert s.verify_enabled is True


def test_numeric_values_are_clamped_and_fall_back_on_garbage():
    s = Settings.from_env({"PAI_VERIFY_REVISIONS": "99", "PAI_TAIL_LENGTH": "1"})
    assert s.verify_revisions == 5 and s.tail_length == 2
    s = Settings.from_env({"PAI_VERIFY_REVISIONS": "many", "PAI_TAIL_LENGTH": "x"})
    assert s.verify_revisions == 1 and s.tail_length == 10


def test_data_dir_and_timezone():
    s = Settings.from_env({"PAI_DATA_DIR": "/srv/pai", "PAI_TIMEZONE": "Europe/Belgrade"})
    assert s.data_dir == "/srv/pai" and s.timezone_name == "Europe/Belgrade"


def test_llm_config_from_env_parses_types_and_clamps():
    cfg = llm_config_from_env({
        "PAI_LLM_BASE_URL": "https://example.test/v1",
        "PAI_LLM_MAX_TOKENS": "2048",
        "PAI_LLM_TEMPERATURE": "0.7",
        "PAI_LLM_TOP_P": "bad",
        "PAI_LLM_REASONING_EFFORT": "extreme",
        "PAI_LLM_REASONING_ENABLED": "TRUE",
        "PAI_COMPRESSION_TRIGGER_PCT": "500",
    })
    assert cfg.base_url == "https://example.test/v1"
    assert cfg.max_tokens == 2048 and cfg.temperature == 0.7
    assert cfg.top_p is None
    assert cfg.reasoning_effort == "medium"
    assert cfg.reasoning_enabled is True
    assert cfg.compression_trigger_pct == 95
