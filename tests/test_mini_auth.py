"""Tests for Telegram Mini App HMAC verification (verify_init_data)."""

import hashlib
import hmac
import time
import urllib.parse
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from web.mini import INIT_DATA_TTL, verify_init_data

BOT_TOKEN = '0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'


def _make_init_data(tg_id=123456, extra=None, token=BOT_TOKEN):
    """Build a valid Telegram Mini App initData string."""
    auth_date = str(int(time.time()))
    user = urllib.parse.quote('{"id":' + str(tg_id) + ',"first_name":"Test"}')
    pairs = {'auth_date': auth_date, 'user': user}
    if extra:
        pairs.update(extra)
    check_string = '\n'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    pairs['hash'] = h
    return '&'.join(f'{k}={v}' for k, v in pairs.items())


@patch('web.mini.config')
def test_valid_init_data(mock_config):
    mock_config.BOT_TOKEN = BOT_TOKEN
    init_data = _make_init_data(tg_id=9999)
    result = verify_init_data(init_data)
    assert result == 9999


@patch('web.mini.config')
def test_tampered_data(mock_config):
    mock_config.BOT_TOKEN = BOT_TOKEN
    init_data = _make_init_data(tg_id=12345)
    # Tamper with the user value after the HMAC was computed
    tampered = init_data.replace('12345', '99999')
    with pytest.raises(HTTPException) as exc_info:
        verify_init_data(tampered)
    assert exc_info.value.status_code == 403


@patch('web.mini.config')
def test_expired_timestamp(mock_config):
    mock_config.BOT_TOKEN = BOT_TOKEN
    old_auth_date = str(int(time.time()) - INIT_DATA_TTL - 1)
    user = urllib.parse.quote('{"id":12345,"first_name":"Test"}')
    pairs = {'auth_date': old_auth_date, 'user': user}
    check_string = '\n'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    pairs['hash'] = h
    init_data = '&'.join(f'{k}={v}' for k, v in pairs.items())
    with pytest.raises(HTTPException) as exc_info:
        verify_init_data(init_data)
    assert exc_info.value.status_code == 403


@patch('web.mini.config')
def test_missing_fields_empty(mock_config):
    mock_config.BOT_TOKEN = BOT_TOKEN
    with pytest.raises(HTTPException) as exc_info:
        verify_init_data('')
    assert exc_info.value.status_code == 403


@patch('web.mini.config')
def test_missing_fields_partial(mock_config):
    mock_config.BOT_TOKEN = BOT_TOKEN
    with pytest.raises(HTTPException) as exc_info:
        verify_init_data('key=val')
    assert exc_info.value.status_code == 403


@patch('web.mini.config')
def test_bad_bot_token(mock_config):
    mock_config.BOT_TOKEN = 'wrong:token'
    init_data = _make_init_data(tg_id=12345)
    with pytest.raises(HTTPException) as exc_info:
        verify_init_data(init_data)
    assert exc_info.value.status_code == 403
