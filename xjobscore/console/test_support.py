"""Inert runtime collaborators for Console route tests."""

from types import SimpleNamespace

from xjobscore.console.app import create_app as runtime_app
from xjobscore.console.db import CatalogDB
from xjobscore.console.policy import PolicyStore
from xjobscore.console.service import ConsoleService
from xjobscore.console.token_heartbeat import TokenHeartbeatRunner
from xjobscore.net import DEFAULT_USER_AGENT


class IdleScheduler:
    def status(self):
        return {"alive": False, "last_cycle_at": None, "last_polled": None,
                "consecutive_errors": 0, "last_error": None}

    def nudge(self):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


def create_app(*, store, catalog=None, policy=None, user_agent=None,
               runner=None, token_runner=None, token_sweep=None):
    policy = policy if policy is not None else PolicyStore(None)
    catalog = catalog if catalog is not None else CatalogDB(policy)
    scheduler = runner if runner is not None else IdleScheduler()
    heartbeat = token_runner if token_runner is not None else TokenHeartbeatRunner(policy, enabled=False)

    async def start():
        await catalog.connect()
        await store.initialize()
        await policy.start()
        await scheduler.start()
        await heartbeat.start()

    async def stop():
        await heartbeat.stop()
        await scheduler.stop()
        await policy.close()
        await store.close()
        await catalog.disconnect()

    async def status():
        return {"ready": bool(policy.status().get("connected")),
                "lock_owned": False, "postgres": policy.status(),
                "scheduler": scheduler.status(), "pipeline": None, "heartbeat": None}

    runtime = SimpleNamespace(
        result_store=store, policy=policy, catalog=catalog,
        console=ConsoleService(catalog, user_agent=user_agent or DEFAULT_USER_AGENT,
                                   result_store=store, company_store=policy),
        scheduler=scheduler, heartbeat=heartbeat,
        gold_worker=SimpleNamespace(status=lambda: None, nudge=lambda: None),
        reconciler=SimpleNamespace(nudge=lambda: None, status=lambda: {}),
        start=start, stop=stop, status=status,
    )
    return runtime_app(runtime=runtime, token_sweep=token_sweep)
