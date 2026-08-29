"""Basename recipient resolution for tips and x402.

`name.base.eth` resolves on-chain to an address; the address maps to a
Tippy user through EITHER an external linked wallet (wallet_links) OR a
custodial in-bot wallet (user_wallets). Unlinked names are refused with a
clear message (money must never be sent to a name nobody can claim).

The chain resolver is monkeypatched — these tests verify the mapping and
caching logic, not the RPC.
"""

import pytest

from bot import tip_targets
from bot.ledger import Ledger

TEST_DB_URL = "postgresql://tipbot:tipbot@localhost:5432/tipbot_test"
ALICE, BOB = 3101, 3102
ALICE_ADDR = "0x" + "a1" * 20
BOB_CUSTODIAL = "0x" + "b2" * 20
UNLINKED = "0x" + "c3" * 20


@pytest.fixture()
def l():
    ledger = Ledger(TEST_DB_URL)
    ledger._conn.execute("TRUNCATE users, wallet_links, user_wallets, link_nonces RESTART IDENTITY")
    ledger._conn.commit()
    yield ledger
    ledger.close()


def _setup_users(ledger):
    """Alice linked her external wallet; Bob owns a custodial in-bot wallet."""
    ledger.ensure_user(ALICE, "alice")
    ledger.ensure_user(BOB, "bob")
    ledger._conn.execute(
        "INSERT INTO wallet_links (tg_id, address) VALUES (%s, %s)",
        (ALICE, ALICE_ADDR),
    )
    ledger._conn.execute(
        "INSERT INTO user_wallets (tg_id, address, key_enc, seed_enc, slot, active) "
        "VALUES (%s, %s, 'enc', 'enc', 1, true)",
        (BOB, BOB_CUSTODIAL),
    )
    ledger._conn.commit()


@pytest.fixture()
def resolver(l, monkeypatch):
    """Patch the chain resolver + the async ledger proxy tip_targets uses."""
    from bot.ledger import AsyncLedger

    addresses = {}
    monkeypatch.setattr(tip_targets, "resolve_basename_sync",
                        lambda name: addresses.get(name.strip().lower().lstrip("@")))
    monkeypatch.setattr(tip_targets, "ledger", AsyncLedger(l))
    return addresses


def _name(owner_addr):
    return {"alice.base.eth": ALICE_ADDR, "bob.base.eth": BOB_CUSTODIAL,
            "stranger.base.eth": UNLINKED}.get(owner_addr, owner_addr)


@pytest.mark.asyncio
async def test_basename_to_linked_wallet(l, resolver):
    _setup_users(l)
    resolver["alice.base.eth"] = ALICE_ADDR
    tg, err = await tip_targets.resolve_tip_target("alice.base.eth")
    assert (tg, err) == (ALICE, None)


@pytest.mark.asyncio
async def test_basename_to_custodial_wallet(l, resolver):
    _setup_users(l)
    resolver["bob.base.eth"] = BOB_CUSTODIAL
    tg, err = await tip_targets.resolve_tip_target("@bob.base.eth")  # @ stripped
    assert (tg, err) == (BOB, None)


@pytest.mark.asyncio
async def test_basename_unlinked_refused(l, resolver):
    _setup_users(l)
    resolver["stranger.base.eth"] = UNLINKED
    tg, err = await tip_targets.resolve_tip_target("stranger.base.eth")
    assert tg is None and err == "basename_unknown"


@pytest.mark.asyncio
async def test_basename_unregistered_refused(l, resolver):
    _setup_users(l)
    # resolver returns None for names nobody registered (miss = no address)
    tg, err = await tip_targets.resolve_tip_target("ghost.base.eth")
    assert tg is None and err == "basename_unknown"


@pytest.mark.asyncio
async def test_non_basename_falls_back(l, resolver):
    """Regular Telegram-style targets return (None, None): the caller falls
    back to find_by_username — basenames never shadow them."""
    tg, err = await tip_targets.resolve_tip_target("alice")
    assert (tg, err) == (None, None)


@pytest.mark.asyncio
async def test_resolution_is_cached(l, resolver, monkeypatch):
    _setup_users(l)
    tip_targets._cache.clear()  # module-level cache: tests must not leak into it
    calls = []
    import bot.tip_targets as tt

    def counting(name):
        calls.append(name)
        return ALICE_ADDR

    monkeypatch.setattr(tt, "resolve_basename_sync", counting)
    tg, err = await tt.resolve_tip_target("cached.base.eth")
    assert tg == ALICE
    tg, err = await tt.resolve_tip_target("cached.base.eth")
    assert tg == ALICE
    assert len(calls) == 1, "second lookup must hit the cache"


@pytest.mark.asyncio
async def test_display_name_reverse_resolution(l, resolver, monkeypatch):
    """A username-less user with a linked address displays their primary
    basename (reverse resolution, cached)."""
    _setup_users(l)
    l._conn.execute("UPDATE users SET username = NULL WHERE tg_id = %s", (ALICE,))
    l._conn.commit()
    import bot.tip_targets as tt
    tip_targets._cache.clear()
    tip_targets._reverse_cache.clear()

    class FakeLedger:
        async def linked_address(self, tg_id):
            # matches Ledger.linked_address: a plain address string
            return ALICE_ADDR

        async def get_active_wallet(self, tg_id):
            return None

    monkeypatch.setattr(tt, "ledger", FakeLedger())
    monkeypatch.setattr(tt, "reverse_basename_sync", lambda addr: "alice.base.eth")

    name = await tt.display_name_for(ALICE)
    assert name == "alice.base.eth"
    monkeypatch.setattr(tt, "reverse_basename_sync",
                        lambda addr: (_ for _ in ()).throw(AssertionError("must be cached")))
    assert await tt.display_name_for(ALICE) == "alice.base.eth"


@pytest.mark.asyncio
async def test_display_name_none_without_addresses(l, monkeypatch):
    l.ensure_user(BOB, "bob")
    l._conn.execute("UPDATE users SET username = NULL WHERE tg_id = %s", (BOB,))
    l._conn.commit()
    import bot.tip_targets as tt
    tip_targets._reverse_cache.clear()

    class NoWalletLedger:
        async def linked_address(self, tg_id):
            return None

        async def get_active_wallet(self, tg_id):
            return None

    monkeypatch.setattr(tt, "ledger", NoWalletLedger())
    assert await tt.display_name_for(BOB) is None
