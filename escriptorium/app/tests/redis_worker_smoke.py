"""Opt-in CI smoke against disposable Redis, without application data or providers.

Run from app/: ESCRIPT_REDIS_SMOKE=1 python tests/redis_worker_smoke.py
Uses normal Django settings, not test_settings' eager tasks and dummy cache.
"""
import asyncio
from importlib import metadata
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

from packaging.markers import default_environment
from packaging.requirements import Requirement

if os.environ.get('ESCRIPT_REDIS_SMOKE') != '1':
    raise RuntimeError('Run only with ESCRIPT_REDIS_SMOKE=1 and disposable Redis.')

APP_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP_DIR), str(APP_DIR / 'apps')]
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'escriptorium.settings')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import cache  # noqa: E402
from escriptorium.celery import app  # noqa: E402

TASK_NAME = 'escript_ci.redis_smoke'
# This transport probe has no user/document, so it must not create TaskReports.
settings.REPORTING_TASKS_BLACKLIST = [*settings.REPORTING_TASKS_BLACKLIST, TASK_NAME]


@app.task(name=TASK_NAME)
def echo(token):
    return {'token': token, 'pid': os.getpid()}


def check_redis_extras():
    environment = {**default_environment(), 'extra': 'redis'}
    for package in ('celery', 'kombu'):
        for raw in metadata.requires(package) or []:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            installed = metadata.version(requirement.name)
            if not requirement.specifier.contains(installed):
                raise RuntimeError(f'{package}[redis] requires {raw}; found {installed}')
    versions = ', '.join(f'{p}={metadata.version(p)}' for p in ('celery', 'kombu', 'redis'))
    print(f'OK: Redis extras compatible ({versions})', flush=True)


async def check_notifications():
    from channels.layers import get_channel_layer
    from channels.testing import WebsocketCommunicator
    from users.consumers import NotificationConsumer, get_group_name

    user = SimpleNamespace(pk=2147483647, is_authenticated=True, is_superuser=False)

    async def application(scope, receive, send):
        scope['user'] = user
        await NotificationConsumer.as_asgi()(scope, receive, send)

    layer = get_channel_layer()
    communicator = WebsocketCommunicator(application, '/')
    connected = False
    try:
        connected, _ = await communicator.connect(timeout=10)
        if not connected:
            raise RuntimeError('Notification consumer refused connection')
        token = uuid.uuid4().hex
        await layer.group_send(get_group_name(user.pk), {
            'type': 'notification_message', 'id': token, 'level': 'info',
            'text': token, 'links': [],
        })
        message = await communicator.receive_json_from(timeout=10)
        if message != {'type': 'message', 'id': token, 'level': 'info',
                       'text': token, 'links': []}:
            raise RuntimeError('Redis notification roundtrip changed the message')
    finally:
        if connected:
            await communicator.disconnect(timeout=10)
        await layer.close_pools()
    print('OK: Redis-backed WebSocket consumer notification', flush=True)


def main():
    check_redis_extras()
    if app.conf.task_always_eager:
        raise RuntimeError('This check requires a real worker, not eager mode')
    if settings.CACHES['default']['BACKEND'] != 'django_redis.cache.RedisCache':
        raise RuntimeError('This check requires the real Django Redis cache')

    token = uuid.uuid4().hex
    queue = f'escript-ci-{token}'
    node = f'escript-ci-{token}@localhost'
    logs = Path(tempfile.mkdtemp(prefix='escript-worker-smoke-'))
    environment = os.environ.copy()
    environment['PYTHONPATH'] = os.pathsep.join([
        str(APP_DIR), str(APP_DIR / 'apps'), str(Path(__file__).parent),
        environment.get('PYTHONPATH', ''),
    ])
    worker = None
    result = None
    try:
        with (logs / 'worker.log').open('w') as output:
            worker = subprocess.Popen([
                sys.executable, '-m', 'celery', '-A', 'escriptorium.celery:app',
                'worker', '--include=redis_worker_smoke', '--pool=prefork',
                '--concurrency=1', '--loglevel=INFO', '--queues', queue,
                '--hostname', node, '--without-gossip', '--without-mingle',
                '--without-heartbeat',
            ], env=environment, cwd=logs, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if worker.poll() is not None:
                    raise RuntimeError('Worker exited before becoming ready')
                reply = app.control.inspect(destination=[node], timeout=1).ping() or {}
                if reply.get(node, {}).get('ok') == 'pong':
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError('Worker did not respond within 90 seconds')

            result = echo.apply_async(args=[token], queue=queue, routing_key=queue)
            answer = result.get(timeout=30)
            if (answer.get('token') != token or not isinstance(answer.get('pid'), int)
                    or answer['pid'] in (os.getpid(), worker.pid)):
                raise RuntimeError('Task did not execute in the prefork worker child')
            print('OK: queued task executed in prefork worker and result retrieved', flush=True)

            key = f'escript-ci-{token}'
            try:
                cache.set(key, token, timeout=30)
                if cache.get(key) != token:
                    raise RuntimeError('Django Redis cache roundtrip failed')
            finally:
                cache.delete(key)
            print('OK: Django Redis cache roundtrip', flush=True)
            asyncio.run(asyncio.wait_for(check_notifications(), timeout=30))
    finally:
        failed = sys.exc_info()[0] is not None
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(timeout=5)
        if result is not None:
            try:
                result.forget()
            except Exception:
                if not failed:
                    raise
                print('Could not remove the smoke result during failure cleanup', file=sys.stderr)
        if failed and (logs / 'worker.log').exists():
            print((logs / 'worker.log').read_text()[-12000:], file=sys.stderr)
        print(f'Worker log: {logs / "worker.log"}', flush=True)


if __name__ == '__main__':
    main()
