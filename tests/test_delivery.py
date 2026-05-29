"""Tests for delivery — env-var guards and (mocked) SMTP retry behavior."""
from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest


class TestEmailSendEnvGuard:
    def test_missing_app_password_raises_runtime_error(self, monkeypatch):
        from warren_bot.delivery.email_send import send_email

        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        cfg = {"from_addr": "a@b.com", "to_addr": "c@d.com",
               "smtp_host": "smtp.gmail.com", "smtp_port": 587}
        with pytest.raises(RuntimeError, match="GMAIL_APP_PASSWORD"):
            send_email("subject", "<p>hi</p>", cfg)


class TestEmailSendRetry:
    """Connection-level errors retry; auth errors fail fast."""

    def test_succeeds_on_first_try(self, monkeypatch):
        from warren_bot.delivery import email_send

        monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
        cfg = {"from_addr": "a@b.com", "to_addr": "c@d.com",
               "smtp_host": "smtp.gmail.com", "smtp_port": 587}

        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        with patch.object(email_send.smtplib, "SMTP", return_value=mock_smtp) as smtp_cls:
            email_send.send_email("s", "<p>hi</p>", cfg)
            smtp_cls.assert_called_once()
            mock_smtp.send_message.assert_called_once()

    def test_retries_on_connection_error(self, monkeypatch):
        """SMTPServerDisconnected during connect should retry the whole send."""
        from warren_bot.delivery import email_send

        monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
        cfg = {"from_addr": "a@b.com", "to_addr": "c@d.com",
               "smtp_host": "smtp.gmail.com", "smtp_port": 587}

        mock_ok = MagicMock()
        mock_ok.__enter__.return_value = mock_ok

        call_count = {"n": 0}

        def smtp_factory(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise smtplib.SMTPServerDisconnected("transient")
            return mock_ok

        with patch.object(email_send.smtplib, "SMTP", side_effect=smtp_factory):
            email_send.send_email("s", "<p>hi</p>", cfg)
        assert call_count["n"] == 2  # one failure + one retry
        mock_ok.send_message.assert_called_once()

    def test_auth_error_does_not_retry(self, monkeypatch):
        """SMTPAuthenticationError must NOT retry — auth never fixes itself."""
        from warren_bot.delivery import email_send

        monkeypatch.setenv("GMAIL_APP_PASSWORD", "wrong-pw")
        cfg = {"from_addr": "a@b.com", "to_addr": "c@d.com",
               "smtp_host": "smtp.gmail.com", "smtp_port": 587}

        mock_smtp = MagicMock()
        mock_smtp.__enter__.return_value = mock_smtp
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad password")

        with patch.object(email_send.smtplib, "SMTP", return_value=mock_smtp) as smtp_cls:
            with pytest.raises(smtplib.SMTPAuthenticationError):
                email_send.send_email("s", "<p>hi</p>", cfg)
        # Auth errors raise immediately — only one SMTP construction
        assert smtp_cls.call_count == 1


class TestNotionSyncEnvGuard:
    def test_missing_api_key_raises(self, monkeypatch):
        from warren_bot.delivery.notion_sync import sync_picks

        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="NOTION_API_KEY"):
            sync_picks([], {"database_id": "abc"})

    def test_missing_database_id_raises(self, monkeypatch):
        from warren_bot.delivery.notion_sync import sync_picks

        monkeypatch.setenv("NOTION_API_KEY", "secret")
        with pytest.raises(RuntimeError, match="database_id"):
            sync_picks([], {})
