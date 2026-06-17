"""Тесты разбора вебхука и проверки подписи Meta."""
import hashlib
import hmac

import config
from app import instagram


def test_parse_incoming_extracts_text():
    body = {"entry": [{"messaging": [
        {"sender": {"id": "u1"}, "message": {"text": "привет"}},
    ]}]}
    assert instagram.parse_incoming(body) == [{"sender_id": "u1", "text": "привет"}]


def test_parse_incoming_skips_echo():
    body = {"entry": [{"messaging": [
        {"sender": {"id": "hotel"}, "message": {"text": "ответ", "is_echo": True}},
    ]}]}
    assert instagram.parse_incoming(body) == []


def test_parse_incoming_skips_no_text():
    body = {"entry": [{"messaging": [
        {"sender": {"id": "u1"}, "message": {"attachments": []}},
    ]}]}
    assert instagram.parse_incoming(body) == []


def test_verify_signature_no_secret(monkeypatch):
    monkeypatch.setattr(config, "IG_APP_SECRET", "")
    assert instagram.verify_signature(b"payload", "anything") is True


def test_verify_signature_valid_and_invalid(monkeypatch):
    monkeypatch.setattr(config, "IG_APP_SECRET", "s3cr3t")
    payload = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"s3cr3t", payload, hashlib.sha256).hexdigest()
    assert instagram.verify_signature(payload, sig) is True
    assert instagram.verify_signature(payload, "sha256=deadbeef") is False
    assert instagram.verify_signature(payload, "") is False
