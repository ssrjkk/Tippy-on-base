"""Regression tests for the combined deploy runner (deploy/run.py).

The critical invariants:
  1. The watcher set in run.py stays in sync with bot/main.py — the
     x402_sweep watcher previously existed only in main.py and silently never
     ran in combined (deploy) mode, so x402 funds were never consolidated.
  2. A watcher that dies unexpectedly must surface loudly and stop the
     process (via the stop event) instead of the system limping on with a
     dead money-path; a cancelled watcher is the normal shutdown path and
     must NOT trigger that.
"""

import asyncio

from deploy import run as deploy_run


def test_watcher_set_matches_bot_main():
    """The deployed watcher names must equal bot.main's task set exactly.

    bot.main builds its tasks by name in order; any name added there and not
    mirrored here would mean a background money-path never runs in the
    Docker/deploy (combined) entrypoint. The x402_sweep watcher previously
    existed only in main.py and silently never ran in combined mode.
    """
    main_names = {
        "deposit",
        "withdraw",
        "batch_withdraw",
        "market",
        "channel",
        "create2_sweep",
        "x402_sweep",
        "housekeeping",
        "solvency",
        "onchain",
        "x402_reconcile",
        "notification_outbox",
    }
    run_names = {name for name, _ in deploy_run.WATCHERS}
    assert run_names == main_names


def test_watcher_factories_produce_awaitables():
    """Every WATCHERS factory must return a coroutine when called with dummy
    (bot, ledger) arguments — a None/exception here means the deployed bot
    would hang or crash on startup."""
    for name, factory in deploy_run.WATCHERS:
        coro = factory(None, None)  # type: ignore[arg-type]
        assert asyncio.iscoroutine(coro), f"watcher '{name}' is not awaitable"
        coro.close()


def test_watcher_done_callback_sets_stop_on_death():
    """A watcher that dies with an exception must flag the stop event."""
    stop = asyncio.Event()

    async def _die():
        raise RuntimeError("boom")

    async def _scene():
        task = asyncio.create_task(_die())
        task.add_done_callback(lambda t: deploy_run._watcher_done("test_die", t, stop))
        await asyncio.sleep(0.01)

    asyncio.run(_scene())
    assert stop.is_set()


def test_watcher_done_callback_sets_stop_on_early_return():
    """A watcher that returns normally (no exception) is still a death: the
    background loop stopped running and must trigger shutdown."""
    stop = asyncio.Event()

    async def _return():
        return None

    async def _scene():
        task = asyncio.create_task(_return())
        task.add_done_callback(lambda t: deploy_run._watcher_done("test_return", t, stop))
        await asyncio.sleep(0.01)

    asyncio.run(_scene())
    assert stop.is_set()


def test_watcher_done_callback_ignores_cancellation():
    """A cancelled watcher is the graceful-shutdown path and must NOT stop the
    process (the runner's own finally block already cancelled them)."""
    stop = asyncio.Event()
    started = asyncio.Event()

    async def _sleep():
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            pass

    async def _scene():
        task = asyncio.create_task(_sleep())
        await started.wait()
        task.cancel()
        task.add_done_callback(lambda t: deploy_run._watcher_done("test_cancel", t, stop))
        await asyncio.sleep(0.01)

    asyncio.run(_scene())
    assert not stop.is_set()


def test_signal_handlers_registered(monkeypatch):
    """_run_combined must register SIGTERM/SIGINT handlers — otherwise a
    container SIGTERM kills the process instead of allowing a graceful ledger
    close. The loop's add_signal_handler is stubbed per-instance so the test
    runs on Windows too (where the real method raises NotImplementedError)."""
    import signal

    registered = []

    async def _run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda sig, cb: registered.append(sig))

        async def _fake_bot(stop):
            assert stop is not None and not stop.is_set()

        async def _fake_web(stop):
            assert stop is not None
            return

        monkeypatch.setattr(deploy_run, "_start_bot_polling", _fake_bot)
        monkeypatch.setattr(deploy_run, "_start_web_server", _fake_web)
        # config.validate() may try RPC; stub it to a no-op, and the hot-wallet
        # print reads config.HOT_WALLET_KEY (module-level `config` import).
        from bot import config as _config

        monkeypatch.setattr(_config, "validate", lambda: None)
        monkeypatch.setattr(_config, "HOT_WALLET_KEY", None)
        # _run_combined imports public_base_url lazily; stub the real module.
        import types

        fake_mini = types.ModuleType("web.mini")
        fake_mini.public_base_url = lambda: "http://localhost"
        import sys

        monkeypatch.setitem(sys.modules, "web.mini", fake_mini)
        await deploy_run._run_combined()

    asyncio.run(_run())
    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered
