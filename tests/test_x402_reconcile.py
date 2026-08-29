"""Tests for reconcile_stale_x402 — the EIP-3009 reservation sweep."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def _receive(monkeypatch):
    """Ensure _x402_receive_address returns a valid address."""
    from web import x402
    monkeypatch.setattr(
        x402, "_x402_receive_address", lambda: "0x" + "aa" * 20
    )


def _row(tx_hash, payer="0x" + "bb" * 20, tg=777):
    return {
        "tx_hash": tx_hash,
        "sender": payer,
        "recipient_tg": str(tg),
    }


@pytest.mark.usefixtures("_receive")
@pytest.mark.asyncio
async def test_finalize_after_nonce_burned():
    """Nonce burned on-chain + settlement found → finalize_x402_credit called."""
    nonce_hex = "ab" * 32
    tx_key = f"auth:{nonce_hex}"
    settlement_tx = "0x" + "cc" * 32

    with patch("web.x402.ledger") as mock_ledger, \
         patch("web.x402.x402_spec") as mock_spec:
        mock_ledger.x402_auth_reservations = AsyncMock(
            return_value=[_row(tx_key)]
        )
        mock_ledger.finalize_x402_credit = AsyncMock(return_value=True)
        mock_ledger.release_x402_auth = AsyncMock()

        mock_spec.authorization_burned = MagicMock(return_value=True)
        mock_spec.find_settlement_by_nonce = MagicMock(
            return_value={"tx": settlement_tx, "value": 5_000_000}
        )

        from web.x402 import reconcile_stale_x402
        result = await reconcile_stale_x402()

        assert result == 1
        mock_ledger.finalize_x402_credit.assert_awaited_once_with(
            tx_key, settlement_tx, 777, 5_000_000, "0x" + "bb" * 20
        )
        mock_ledger.release_x402_auth.assert_not_awaited()


@pytest.mark.usefixtures("_receive")
@pytest.mark.asyncio
async def test_release_unburned_nonce():
    """Nonce NOT burned → release_x402_auth called, no finalize."""
    nonce_hex = "ab" * 32
    tx_key = f"auth:{nonce_hex}"

    with patch("web.x402.ledger") as mock_ledger, \
         patch("web.x402.x402_spec") as mock_spec:
        mock_ledger.x402_auth_reservations = AsyncMock(
            return_value=[_row(tx_key)]
        )
        mock_ledger.release_x402_auth = AsyncMock()
        mock_ledger.finalize_x402_credit = AsyncMock()

        mock_spec.authorization_burned = MagicMock(return_value=False)
        mock_spec.find_settlement_by_nonce = MagicMock()

        from web.x402 import reconcile_stale_x402
        result = await reconcile_stale_x402()

        assert result == 0
        mock_ledger.release_x402_auth.assert_awaited_once_with(tx_key)
        mock_ledger.finalize_x402_credit.assert_not_awaited()
        mock_spec.find_settlement_by_nonce.assert_not_called()


@pytest.mark.usefixtures("_receive")
@pytest.mark.asyncio
async def test_retry_on_rpc_failure():
    """authorization_burned returns None (RPC down) → row kept for next sweep."""
    nonce_hex = "ab" * 32
    tx_key = f"auth:{nonce_hex}"

    with patch("web.x402.ledger") as mock_ledger, \
         patch("web.x402.x402_spec") as mock_spec:
        mock_ledger.x402_auth_reservations = AsyncMock(
            return_value=[_row(tx_key)]
        )
        mock_ledger.release_x402_auth = AsyncMock()
        mock_ledger.finalize_x402_credit = AsyncMock()

        mock_spec.authorization_burned = MagicMock(return_value=None)
        mock_spec.find_settlement_by_nonce = MagicMock()

        from web.x402 import reconcile_stale_x402
        result = await reconcile_stale_x402()

        assert result == 0
        mock_ledger.release_x402_auth.assert_not_awaited()
        mock_ledger.finalize_x402_credit.assert_not_awaited()
        mock_spec.find_settlement_by_nonce.assert_not_called()


@pytest.mark.usefixtures("_receive")
@pytest.mark.asyncio
async def test_malformed_nonce_released():
    """Bad nonce format (not valid hex after 'auth:') → release called."""
    tx_key = "auth:not_hex!!"

    with patch("web.x402.ledger") as mock_ledger, \
         patch("web.x402.x402_spec") as mock_spec:
        mock_ledger.x402_auth_reservations = AsyncMock(
            return_value=[_row(tx_key)]
        )
        mock_ledger.release_x402_auth = AsyncMock()
        mock_ledger.finalize_x402_credit = AsyncMock()

        mock_spec.authorization_burned = MagicMock()
        mock_spec.find_settlement_by_nonce = MagicMock()

        from web.x402 import reconcile_stale_x402
        result = await reconcile_stale_x402()

        assert result == 0
        mock_ledger.release_x402_auth.assert_awaited_once_with(tx_key)
        mock_ledger.finalize_x402_credit.assert_not_awaited()
        mock_spec.authorization_burned.assert_not_called()


@pytest.mark.usefixtures("_receive")
@pytest.mark.asyncio
async def test_already_settled_noop():
    """finalize_x402_credit returns False (already settled) → no double-credit."""
    nonce_hex = "ab" * 32
    tx_key = f"auth:{nonce_hex}"
    settlement_tx = "0x" + "cc" * 32

    with patch("web.x402.ledger") as mock_ledger, \
         patch("web.x402.x402_spec") as mock_spec:
        mock_ledger.x402_auth_reservations = AsyncMock(
            return_value=[_row(tx_key)]
        )
        mock_ledger.finalize_x402_credit = AsyncMock(return_value=False)
        mock_ledger.release_x402_auth = AsyncMock()

        mock_spec.authorization_burned = MagicMock(return_value=True)
        mock_spec.find_settlement_by_nonce = MagicMock(
            return_value={"tx": settlement_tx, "value": 5_000_000}
        )

        from web.x402 import reconcile_stale_x402
        result = await reconcile_stale_x402()

        assert result == 0
        mock_ledger.finalize_x402_credit.assert_awaited_once()
        mock_ledger.release_x402_auth.assert_not_awaited()
