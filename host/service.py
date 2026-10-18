"""Install/remove the macOS LaunchAgent for the Minna no Nihongo ESP32 Bridge."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'com.wuhao.esp-minna-nihongo'
PLIST = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
PYTHON = sys.executable


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['launchctl', *args], capture_output=True, text=True, check=check)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'remove', 'restart', 'status'])
    action = parser.parse_args().action
    domain = f'gui/{os.getuid()}'

    if action == 'status':
        p = run(['print', f'{domain}/{LABEL}'], False)
        for line in p.stdout.splitlines():
            if any(k in line for k in ('state =', 'pid =', 'last exit code =')):
                print(line.strip())
        if p.returncode:
            print('Bridge not loaded')
        return

    if action == 'restart':
        run(['kickstart', '-k', f'{domain}/{LABEL}'])
        print('Bridge restarted')
        return

    stopped = run(['bootout', f'{domain}/{LABEL}'], False)
    if stopped.returncode == 0:
        for _ in range(50):
            if run(['print', f'{domain}/{LABEL}'], False).returncode:
                break
            time.sleep(0.1)

    if action == 'remove':
        if PLIST.exists():
            PLIST.unlink()
        print('Removed bridge autostart; device app and local source kept')
        return

    assert Path(PYTHON).exists()
    (ROOT / 'evidence').mkdir(exist_ok=True)
    data = {
        'Label': LABEL,
        'ProgramArguments': [PYTHON, str(ROOT / 'host/bridge.py')],
        'WorkingDirectory': str(ROOT),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'StandardOutPath': str(ROOT / 'evidence/bridge.log'),
        'StandardErrorPath': str(ROOT / 'evidence/bridge-errors.log'),
        'EnvironmentVariables': {
            'PATH': str(Path.home() / '.local/bin')
            + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        },
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(data))
    for attempt in range(5):
        result = run(['bootstrap', domain, str(PLIST)], False)
        if not result.returncode:
            break
        if run(['print', f'{domain}/{LABEL}'], False).returncode == 0:
            break
        if attempt == 4:
            raise RuntimeError('Bridge autostart failed to load')
        time.sleep(0.5)
    print('Installed and started:', PLIST)


if __name__ == '__main__':
    main()
