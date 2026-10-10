"""No live HTTP: shared verification rate and full Workable cooldown."""
import asyncio
from pathlib import Path
import sqlite3
import sys
import time

import httpx
import pytest

from xjobscore import workable_policy as policy
from xjobscore.net import Net, NetError
from xjobscore.console.network import ConsoleNet
from keyscan.probe import default_request


@pytest.fixture
def shared(tmp_path, monkeypatch):
    gate = policy.SharedPolicy(tmp_path/'state.sqlite3', interval=0.04)
    monkeypatch.setattr(policy, 'policy', lambda: gate)
    return gate


async def test_separate_clients_share_verification_spacing(shared):
    starts = []
    async def respond(request):
        starts.append(time.monotonic())
        return httpx.Response(200, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        poller = Net(user_agent='test', min_interval_s=0)
        console = ConsoleNet(user_agent='test', min_interval_s=0)
        poller._client = console._client = client
        await asyncio.gather(
            poller.get('https://apply.workable.com/api/v1/widget/accounts/a'),
            console.get('https://apply.workable.com/api/v1/widget/accounts/b'),
            default_request(client,'GET','https://apply.workable.com/api/v1/widget/accounts/c'),
        )
    assert len(starts) == 3
    assert min(b-a for a,b in zip(starts,starts[1:])) >= 0.035


async def test_polling_does_not_spend_verification_allowance(shared):
    shared.interval = 10
    await shared.acquire(validation=True)
    await asyncio.wait_for(shared.acquire(validation=False), timeout=0.5)
    assert not policy.verification('https://apply.workable.com/api/v1/widget/accounts/a?details=true')
    assert not policy.verification('https://apply.workable.com/api/v1/widget/accounts/a', {'details':True})
    assert policy.verification('https://apply.workable.com/api/v1/widget/accounts/a')
    assert not policy.matches('https://apply.workable.com.evil.example/a')


async def test_reading_cooldown_does_not_create_state(tmp_path):
    path = tmp_path / 'missing' / 'state.sqlite3'
    assert await policy.SharedPolicy(path).cooldown_until() == 0
    assert not path.parent.exists()


async def test_cooldown_deadline_survives_restart_without_spending_allowance(shared):
    await shared.hold(86400)
    deadline = await shared.cooldown_until()
    restarted = policy.SharedPolicy(shared.path)
    assert await restarted.cooldown_until() == deadline
    assert deadline > time.time() + 86390
    with sqlite3.connect(shared.path) as db:
        assert db.execute('SELECT next_start FROM policy WHERE id=1').fetchone() == (0,)


@pytest.mark.parametrize('kind',['net','console','default'])
async def test_first_429_stops_retries_and_blocks_other_clients(shared,kind):
    calls = []
    async def refuse(request):
        calls.append(request)
        return httpx.Response(429,headers={'retry-after':'86400'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        if kind == 'default':
            response = await default_request(client,'GET','https://apply.workable.com/api/v1/widget/accounts/a')
            assert response.status_code == 429
        else:
            net = (Net if kind == 'net' else ConsoleNet)(user_agent='test',min_interval_s=0,max_retries=5)
            net._client = client
            with pytest.raises(NetError) as caught:
                await net.get('https://apply.workable.com/api/v1/widget/accounts/a?details=true')
            assert caught.value.status == 429
        assert len(calls) == 1
        other = Net(user_agent='test',min_interval_s=0)
        other._client = client
        with pytest.raises(NetError):
            await other.get('https://apply.workable.com/api/v1/widget/accounts/b?details=true')
        deferred = await default_request(client,'GET','https://apply.workable.com/api/v1/widget/accounts/c')
        assert deferred.status_code == 429
        assert len(calls) == 1
    # Independent policy instances keep the full server delay after restart.
    restarted = policy.SharedPolicy(shared.path)
    with pytest.raises(policy.CoolingDown) as caught:
        await restarted.acquire(validation=False)
    assert caught.value.remaining >= 86390


async def test_zero_retry_after_uses_nonzero_shared_cooldown(shared):
    response = httpx.Response(429,headers={'retry-after':'0'},request=httpx.Request('GET','https://apply.workable.com/a'))
    await policy.note_response(response)
    with pytest.raises(policy.CoolingDown) as caught:
        await shared.acquire(validation=False)
    assert caught.value.remaining >= 890


async def test_verification_spacing_shared_across_processes(tmp_path):
    code = ('import asyncio,time,sys; from pathlib import Path; '
            'from xjobscore.workable_policy import SharedPolicy; '
            'asyncio.run(SharedPolicy(Path(sys.argv[1]),interval=0.12).acquire(validation=True)); '
            'print(time.time())')
    async def worker():
        proc = await asyncio.create_subprocess_exec(sys.executable,'-B','-c',code,str(tmp_path/'cross-process.sqlite3'),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err = await proc.communicate()
        assert proc.returncode == 0, err.decode()
        return float(out)
    starts = sorted(await asyncio.gather(worker(),worker()))
    assert starts[1]-starts[0] >= 0.10
