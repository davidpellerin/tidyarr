"""Every test gets its own empty settings and instance files, so none touches the real ones in ~/.config or ~/.local/state."""
import pytest

from tidyarr import config, instance


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PATH", tmp_path / "settings" / "config.json")
    monkeypatch.setattr(instance, "PATH", tmp_path / "state" / "instance.json")
    return config.PATH
