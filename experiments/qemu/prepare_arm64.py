#!/usr/bin/env python3
"""Download the recorded Debian ARM64 guest inputs into a lab directory."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import urllib.request


def download(url, destination, expected):
    if destination.exists():
        data = destination.read_bytes()
    else:
        data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f'Hash mismatch for {url}; input changed, review before updating manifest')
    destination.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=pathlib.Path)
    args = parser.parse_args()
    base = args.directory.resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = base / 'root'
    root.mkdir(exist_ok=True)
    manifest = json.loads(pathlib.Path(__file__).with_name('arm64-inputs.json').read_text())
    for package in manifest['packages']:
        archive = base / (package['Package'] + '.deb')
        download('https://deb.debian.org/debian/' + package['Filename'], archive, package['SHA256'])
        subprocess.run(['dpkg-deb', '-x', str(archive), str(root)], check=True)
    for path, target in [('lib', 'usr/lib'), ('bin/busybox', '../usr/bin/busybox'),
                         ('usr/bin/python3', 'python3.13')]:
        link = root / path
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.is_symlink() and not link.exists():
            link.symlink_to(target)
    kernel = manifest['kernel']
    download(kernel['url'], base / 'linux', kernel['sha256'])
    print(f'Guest kernel: {base / "linux"}\nGuest userspace: {root}')


if __name__ == '__main__':
    main()
