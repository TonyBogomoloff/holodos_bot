"""Build an immutable committed release; install it as the existing unprivileged app owner."""
import argparse
import fcntl
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

APP = Path('/home/tony/Desktop/_code/holodos_bot')
SERVICE = 'holodos-bot.service'


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(candidate):
    manifest = json.loads((candidate / 'release.json').read_text())
    for name, expected in manifest['files'].items():
        path = candidate / name
        if path.is_symlink() or not path.resolve().is_relative_to(candidate.resolve()):
            raise RuntimeError('unsafe_release_path')
        if digest(path.read_bytes()) != expected:
            raise RuntimeError('release_hash_mismatch: ' + name)
    return manifest


def build(args):
    if run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout.strip():
        raise RuntimeError('commit_changes_before_build')
    sha = run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    names = run(['git', 'ls-tree', '-r', '--name-only', sha], capture_output=True, text=True).stdout.splitlines()
    files = {name: run(['git', 'show', f'{sha}:{name}'], capture_output=True).stdout for name in names}
    manifest = {'sha': sha, 'base': args.base, 'files': {name: digest(data) for name, data in files.items()}}
    with tarfile.open(args.output, 'w:gz') as archive:
        for name, data in {**files, 'release.json': json.dumps(manifest, indent=2).encode()}.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
    print(json.dumps({'sha': sha, 'archive': str(args.output), 'sha256': digest(args.output.read_bytes())}))


def prepare(args):
    candidate = args.candidate.resolve()
    manifest = verify(candidate)
    if candidate != APP / '.releases' / manifest['sha']:
        raise RuntimeError('unexpected_candidate_directory')
    run(['python3', '-m', 'venv', str(candidate / '.venv')])
    python = str(candidate / '.venv/bin/python')
    run([python, '-m', 'pip', 'install', '-q', '-r', 'requirements.lock'], cwd=candidate)
    run([python, '-m', 'pip', 'install', '-q', '--no-deps', '-e', '.'], cwd=candidate)
    run([python, '-m', 'ruff', 'check', 'src', 'scripts', 'tests'], cwd=candidate)
    run([python, '-m', 'pytest', '-q', *args.tests], cwd=candidate)
    config = APP / 'config.json'
    if not config.exists():
        value = json.loads((candidate / 'config.example.json').read_text())
        value['data_dir'] = str(APP / 'data')
        with config.open('x') as f:
            json.dump(value, f, indent=2)
    config.chmod(0o600)
    run([python, '-m', 'holodos_bot', 'check', '--config', str(config)], cwd=candidate)
    verify(candidate)
    receipt = {'sha': manifest['sha'], 'manifest_hash': digest((candidate / 'release.json').read_bytes()),
               'checks': ['ruff', 'pytest', 'telegram_getMe', 'chatgpt_auth'], 'test_paths': args.tests, 'created': time.time()}
    (candidate / 'validation.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))


def install(args):
    candidate = args.candidate.resolve()
    APP.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (APP / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = verify(candidate)
        if candidate != APP / '.releases' / manifest['sha']:
            raise RuntimeError('unexpected_candidate_directory')
        validation = json.loads((candidate / 'validation.json').read_text())
        if validation['sha'] != manifest['sha'] or validation['manifest_hash'] != digest(
                (candidate / 'release.json').read_bytes()):
            raise RuntimeError('candidate_not_validated')
        installed = APP / 'release.json'
        old_receipt = installed.read_bytes() if installed.exists() else None
        old_sha = json.loads(old_receipt)['sha'] if old_receipt else None
        if old_sha != manifest['base']:
            raise RuntimeError('concurrent_source_change')
        current = APP / 'current'
        previous = current.resolve() if current.is_symlink() else None
        if current.exists() and not current.is_symlink():
            raise RuntimeError('unexpected_current_directory')
        if previous:
            previous_manifest = verify(previous)
            if previous_manifest['sha'] != old_sha:
                raise RuntimeError('production_manifest_changed')
        unit = Path.home() / '.config/systemd/user' / SERVICE
        old_unit = unit.read_bytes() if unit.exists() else None
        if old_unit:
            run(['systemctl', '--user', 'stop', SERVICE])
        backups = APP / '.backups'
        backups.mkdir(exist_ok=True, mode=0o700)
        database = APP / 'data/holodos.sqlite3'
        if database.exists():
            with sqlite3.connect(database) as source, sqlite3.connect(
                    backups / f'holodos-{int(time.time())}.sqlite3') as dest:
                source.backup(dest)
        for backup in backups.glob('holodos-*.sqlite3'):
            if backup.stat().st_mtime < time.time() - 86400:
                backup.unlink()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_bytes((candidate / 'deploy' / SERVICE).read_bytes())
        temporary = APP / '.next-release'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(candidate)
        temporary.replace(current)
        try:
            run(['systemctl', '--user', 'daemon-reload'])
            run(['systemctl', '--user', 'enable', '--now', SERVICE])
            run([str(candidate / '.venv/bin/python'), '-m', 'holodos_bot', 'configure',
                 '--config', str(APP / 'config.json')], cwd=candidate)
            time.sleep(3)
            run(['systemctl', '--user', 'is-active', '--quiet', SERVICE])
            run([str(candidate / '.venv/bin/python'), '-m', 'holodos_bot', 'status',
                 '--config', str(APP / 'config.json')], cwd=candidate)
            verify(candidate)
            receipt_next = APP / '.next-receipt'
            receipt_next.write_bytes((candidate / 'release.json').read_bytes())
            receipt_next.replace(installed)
        except BaseException:
            subprocess.run(['systemctl', '--user', 'stop', SERVICE], check=False)
            if previous:
                temporary.symlink_to(previous)
                temporary.replace(current)
            else:
                current.unlink(missing_ok=True)
            if old_unit:
                unit.write_bytes(old_unit)
            else:
                unit.unlink(missing_ok=True)
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
            if previous:
                subprocess.run(['systemctl', '--user', 'start', SERVICE], check=False)
            # Never roll user data backwards after the service has accepted updates.
            raise
        print(json.dumps({'installed_sha': manifest['sha'], 'matches_manifest': True}))


def status(_):
    current = APP / 'current'
    if not current.exists():
        print(json.dumps({'installed': False}))
        return
    manifest = verify(current.resolve())
    service = run(['systemctl', '--user', 'show', SERVICE,
                   '--property=ActiveState,SubState,NRestarts'], capture_output=True, text=True).stdout
    print(json.dumps({'installed': True, 'sha': manifest['sha'], 'matches_manifest': True,
                      'service': dict(line.split('=', 1) for line in service.strip().splitlines())}))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('build')
    command.add_argument('--output', type=Path, required=True)
    command.add_argument('--base', default=None)
    for name in ('prepare', 'install'):
        command = sub.add_parser(name)
        command.add_argument('--candidate', type=Path, required=True)
        if name == 'prepare':
            command.add_argument('--tests', nargs='+', default=['tests'])
    sub.add_parser('status')
    args = parser.parse_args()
    {'build': build, 'prepare': prepare, 'install': install, 'status': status}[args.command](args)
