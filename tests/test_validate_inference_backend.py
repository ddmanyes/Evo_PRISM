"""M4 — validate_inference_backend() 早期失敗測試。

驗證 backend 為 claude/google 但對應 API key 為空字串時立即 raise，
避免延遲到第一次 API call 才出現 401。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestValidateInferenceBackend:
    def test_local_passes_without_keys(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(s, "GOOGLE_API_KEY", "")
        monkeypatch.setattr(s, "INFERENCE_BACKEND", "local")
        s.validate_inference_backend()  # 不該 raise

    def test_claude_missing_key_raises(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "ANTHROPIC_API_KEY", "")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            s.validate_inference_backend("claude")

    def test_claude_with_key_passes(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "ANTHROPIC_API_KEY", "sk-test-abc")
        s.validate_inference_backend("claude")

    def test_google_missing_key_raises(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "GOOGLE_API_KEY", "")
        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
            s.validate_inference_backend("google")

    def test_google_with_key_passes(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "GOOGLE_API_KEY", "AIza-test")
        s.validate_inference_backend("google")

    def test_resolves_from_env_when_no_arg(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(s, "INFERENCE_BACKEND", "claude")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            s.validate_inference_backend()

    def test_explicit_arg_overrides_env(self, monkeypatch):
        import config.settings as s

        # env 說 google（缺 key 會炸），但顯式傳 local 應該過
        monkeypatch.setattr(s, "GOOGLE_API_KEY", "")
        monkeypatch.setattr(s, "INFERENCE_BACKEND", "google")
        s.validate_inference_backend("local")

    def test_case_insensitive(self, monkeypatch):
        import config.settings as s

        monkeypatch.setattr(s, "ANTHROPIC_API_KEY", "")
        with pytest.raises(RuntimeError):
            s.validate_inference_backend("CLAUDE")


class TestAgentClientFactoryFailFast:
    """_get_claude_client / _get_google_client 缺 key 應立即 raise。"""

    def test_claude_client_raises_without_key(self, monkeypatch):
        import config.settings as s
        import server.agent as a

        monkeypatch.setattr(s, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(a, "_claude_client", None)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            a._get_claude_client()

    def test_google_client_raises_without_key(self, monkeypatch):
        import config.settings as s
        import server.agent as a

        monkeypatch.setattr(s, "GOOGLE_API_KEY", "")
        monkeypatch.setattr(a, "_google_client", None)
        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
            a._get_google_client()
