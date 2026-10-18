"""Find the paired ESP-Claw (ESP-Mosaico) on directly connected macOS IPv4 networks."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def mac(value):
    parts = str(value).lower().replace('-', ':').split(':')
    if len(parts) != 6 or any(not re.fullmatch(r'[0-9a-f]{1,2}', x) for x in parts):
        raise ValueError('Invalid device MAC address')
    return ':'.join(f'{int(x, 16):02x}' for x in parts)


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=3, check=True).stdout


def parse_networks(text):
    result = []
    for block in re.split(r'(?m)(?=^[\w]+: flags=)', text):
        name = block.split(':', 1)[0]
        if not re.fullmatch(r'(en\d+|bridge\d+)', name) or 'status: active' not in block:
            continue
        for addr, mask in re.findall(r'\binet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-fA-F]+|[\d.]+)', block):
            if mask.startswith('0x'):
                mask = str(ipaddress.IPv4Address(int(mask, 16)))
            network = ipaddress.IPv4Network(f'{addr}/{mask}', strict=False)
            if not network.is_loopback and network not in [x[1] for x in result]:
                result.append((addr, network))
    return result


def parse_neighbors(text):
    result = []
    for addr, hardware in re.findall(r'\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-fA-F:]+)\b', text):
        try:
            result.append((addr, mac(hardware)))
        except ValueError:
            pass
    return result


class Resolver:
    def __init__(self, config=None):
        self.config = Path(config or ROOT / 'bridge.local.json')
        self.host = None
        self.valid_until = 0
        self.next_search = 0
        self.network_key = None
        self.offsets = {}

    def settings(self):
        return json.loads(self.config.read_text(encoding='utf-8'))

    def remember(self, host, hardware):
        cfg = self.settings()
        if cfg.get('device_host') == host and cfg.get('device_mac') == hardware:
            return
        cfg.update(device_host=host, device_mac=hardware)
        tmp = self.config.with_name(self.config.name + '.discovery.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(cfg, out, ensure_ascii=False, indent=2)
            out.write('\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.config)

    def invalidate(self):
        self.host = None
        self.valid_until = 0

    def networks(self):
        return parse_networks(command(['/sbin/ifconfig']))

    def neighbors(self):
        return parse_neighbors(command(['/usr/sbin/arp', '-an']))

    def verify(self, host, hardware, networks):
        try:
            address = ipaddress.IPv4Address(host)
            if not any(address in net for _, net in networks):
                return None
            with LOCAL_HTTP.open(f'http://{host}/api/status', timeout=1.5) as response:
                raw = response.read(8193)
                if len(raw) > 8192:
                    return None
                status = json.loads(raw)
            if not isinstance(status, dict) or not isinstance(status.get('ap_ssid'), str):
                return None
            rows = [m for addr, m in self.neighbors() if addr == host]
            if not rows or len(set(rows)) != 1:
                return None
            actual = rows[0]
            if hardware and actual != hardware:
                return None
            expected = 'esp-claw-' + actual.replace(':', '')[-6:]
            if status['ap_ssid'].lower() != expected or status.get('wifi_connected') is not True:
                return None
            return actual
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def probe(self, host):
        try:
            with socket.create_connection((host, 80), timeout=0.35):
                pass
        except OSError:
            pass

    def scan_candidates(self, networks):
        candidates = []
        for own, net in networks:
            total = max(0, net.num_addresses - 2)
            if not total:
                continue
            key = str(net)
            nearby = ((int(ipaddress.IPv4Address(own)) - int(net.network_address)) // 256) * 256
            start = self.offsets.get(key, nearby % total)
            count = min(256, total)
            for i in range(count):
                host = str(net.network_address + 1 + ((start + i) % total))
                if host != own:
                    candidates.append(host)
            self.offsets[key] = (start + count) % total
        return candidates

    def resolve(self, force=False):
        now = time.monotonic()
        try:
            cfg = self.settings()
            networks = self.networks()
            key = tuple(str(net) for _, net in networks)
            if key != self.network_key:
                self.invalidate()
                self.next_search = 0
                self.offsets = {}
                self.network_key = key
            if not force and self.host and now < self.valid_until:
                return self.host
            hardware = mac(cfg['device_mac']) if cfg.get('device_mac') else None
            hint = cfg.get('device_host')
            candidates = [self.host, hint]
            if hardware:
                candidates.extend(addr for addr, m in self.neighbors() if m == hardware)
            for host in dict.fromkeys(x for x in candidates if x):
                actual = self.verify(host, hardware, networks)
                if actual:
                    self.remember(host, actual)
                    self.host = host
                    self.valid_until = time.monotonic() + 10
                    return host
            self.invalidate()
            if not hardware or (not force and now < self.next_search):
                return None
            self.next_search = now + 30
            candidates = self.scan_candidates(networks)
            with ThreadPoolExecutor(max_workers=24) as pool:
                list(pool.map(self.probe, candidates))
            for host, candidate_mac in self.neighbors():
                if candidate_mac == hardware and self.verify(host, hardware, networks):
                    self.remember(host, hardware)
                    self.host = host
                    self.valid_until = time.monotonic() + 10
                    return host
        except (OSError, ValueError, subprocess.SubprocessError):
            self.invalidate()
        return None


_resolver = Resolver()


def resolve_host(force=False):
    host = _resolver.resolve(force)
    if not host:
        raise ConnectionError('Paired ESP32 not found; connect both devices to the same LAN and retry')
    return host


if __name__ == '__main__':
    found = _resolver.resolve(force=True)
    print(json.dumps({'found': bool(found), 'device_host': found}, ensure_ascii=False))
    raise SystemExit(0 if found else 1)
