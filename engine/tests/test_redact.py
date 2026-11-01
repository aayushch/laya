# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for secret redaction used by validation logs and diagnostics."""

from laya.security.redact import (
    REDACTED,
    is_secret_key,
    redact_secrets,
    sanitize_validation_errors,
)


class TestIsSecretKey:
    def test_issue_listed_fields(self):
        for name in (
            "api_key",
            "api_keys",
            "credentials",
            "token",
            "secret",
            "password",
            "authorization",
            "client_secret",
            "refresh_token",
        ):
            assert is_secret_key(name), name

    def test_camel_and_hyphen_variants(self):
        assert is_secret_key("apiKey")
        assert is_secret_key("clientSecret")
        assert is_secret_key("refresh-token")
        assert is_secret_key("Authorization")

    def test_provider_suffixes(self):
        assert is_secret_key("jira_api_token")
        assert is_secret_key("slack_bot_token")
        assert is_secret_key("oauth_client_secret")

    def test_non_secret_fields(self):
        for name in (
            "provider",
            "name",
            "base_url",
            "platform",
            "keyboard",
            "monkey",
            "key_count",
            "tokens_used",
            "max_tokens",
        ):
            assert not is_secret_key(name), name


class TestRedactSecrets:
    def test_nested_dict_and_list(self):
        payload = {
            "provider": "openai",
            "api_key": "sk-secret-12345",
            "nested": {
                "client_secret": "shh",
                "name": "prod",
            },
            "connections": [
                {"platform": "jira", "credentials": {"email": "a@b.c", "token": "abc"}},
            ],
        }
        redacted = redact_secrets(payload)
        assert redacted["provider"] == "openai"
        assert redacted["api_key"] == REDACTED
        assert redacted["nested"]["client_secret"] == REDACTED
        assert redacted["nested"]["name"] == "prod"
        assert redacted["connections"][0]["platform"] == "jira"
        assert redacted["connections"][0]["credentials"] == REDACTED

    def test_does_not_mutate_original(self):
        payload = {"api_key": "sk-live", "provider": "openai"}
        redact_secrets(payload)
        assert payload["api_key"] == "sk-live"

    def test_non_container_passthrough(self):
        assert redact_secrets("plain") == "plain"
        assert redact_secrets(None) is None
        assert redact_secrets(3) == 3


class TestSanitizeValidationErrors:
    def test_redacts_secret_scalar_input(self):
        errors = [
            {
                "type": "string_type",
                "loc": ("body", "api_key"),
                "msg": "Input should be a string",
                "input": "sk-should-not-leak",
                "url": "https://errors.pydantic.dev/2.12/v/string_type",
            }
        ]
        sanitized = sanitize_validation_errors(errors)
        assert sanitized == [
            {
                "type": "string_type",
                "loc": ["body", "api_key"],
                "msg": "Input should be a string",
                "input": REDACTED,
            }
        ]

    def test_redacts_secret_keys_inside_parent_input(self):
        errors = [
            {
                "type": "missing",
                "loc": ("body", "provider"),
                "msg": "Field required",
                "input": {
                    "api_key": "sk-should-not-leak",
                    "credentials": {"token": "abc"},
                    "name": "prod",
                },
            }
        ]
        sanitized = sanitize_validation_errors(errors)
        assert sanitized[0]["input"]["api_key"] == REDACTED
        assert sanitized[0]["input"]["credentials"] == REDACTED
        assert sanitized[0]["input"]["name"] == "prod"
        assert sanitized[0]["loc"] == ["body", "provider"]

    def test_preserves_non_secret_scalar_input(self):
        errors = [
            {
                "type": "string_type",
                "loc": ("body", "provider"),
                "msg": "Input should be a string",
                "input": 123,
            }
        ]
        sanitized = sanitize_validation_errors(errors)
        assert sanitized[0]["input"] == 123

    def test_bad_entry_does_not_abort(self):
        class Boom(dict):
            def get(self, *args, **kwargs):
                raise RuntimeError("cannot read")

        sanitized = sanitize_validation_errors([Boom(), {"type": "missing", "loc": ("body", "name"), "msg": "Field required"}])
        assert sanitized[0] == {"msg": "validation_error"}
        assert sanitized[1]["loc"] == ["body", "name"]
