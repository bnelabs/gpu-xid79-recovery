#!/usr/bin/env python3
"""Offline ELF64 GSP inventory and recovery-string triage; no device access."""
import argparse
import hashlib
import json
import pathlib
import re
import struct


def span(data, offset, size):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError('ELF range outside input')
    return data[offset:offset + size]


def header(data):
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise ValueError('Expected little-endian ELF64')
    values = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)
    if values[7] != 64:
        raise ValueError('Unexpected ELF header size')
    return values


def sections(data):
    h = header(data)
    offset, stride, count, names = h[5], h[10], h[11], h[12]
    if stride != 64 or not count or names >= count:
        raise ValueError('Unsupported section table (including extended numbering)')
    table = span(data, offset, stride * count)
    entries = [struct.unpack_from('<IIQQQQIIQQ', table, i * stride) for i in range(count)]
    strings = span(data, entries[names][4], entries[names][5])
    result = {}
    for entry in entries:
        name_offset = entry[0]
        if name_offset >= len(strings):
            raise ValueError('Bad section name offset')
        end = strings.find(b'\0', name_offset)
        if end < 0:
            raise ValueError('Unterminated section name')
        name = strings[name_offset:end].decode('ascii')
        if name in result:
            raise ValueError('Duplicate section name')
        if entry[1] != 8:  # NOBITS has no file payload.
            span(data, entry[4], entry[5])
        result[name] = (entry[4], entry[5])
    return result


def embedded(payload):
    images, rejected = [], 0
    for match in re.finditer(b'\x7fELF', payload):
        offset = match.start()
        data = payload[offset:]
        try:
            h = header(data)
            if h[1] != 243:  # EM_RISCV
                raise ValueError('Not RISC-V')
            end = 64
            if h[9]:
                if h[8] != 56:
                    raise ValueError('Unexpected program header size')
                table = span(data, h[4], h[8] * h[9])
                end = max(end, h[4] + len(table))
                for i in range(h[9]):
                    p = struct.unpack_from('<IIQQQQQQ', table, i * 56)
                    span(data, p[2], p[5])
                    end = max(end, p[2] + p[5])
            section_table_valid = False
            if h[11]:
                try:
                    sections(data)
                    section_table_valid = True
                except (ValueError, struct.error, UnicodeDecodeError):
                    pass  # Packed archive may retain stale section-header pointers.
            if not h[9]:
                raise ValueError('No program headers')
            span(data, 0, end)
            images.append(dict(payload_offset=offset, size=end, entry=hex(h[3]),
                               sha256=hashlib.sha256(data[:end]).hexdigest(),
                               architecture_mapping='unresolved',
                               section_table_valid=section_table_valid,
                               extent_kind='ELF header and program-file ranges; not a standalone ELF guarantee'))
        except (ValueError, struct.error, UnicodeDecodeError):
            rejected += 1
    return images, rejected


def inspect(path):
    data = path.read_bytes()
    sec = sections(data)
    offset, size = sec['.fwimage']
    payload = span(data, offset, size)
    images, rejected = embedded(payload)
    hits = []
    for match in re.finditer(rb'[ -~]{8,}', payload):
        if re.search(rb'reset|recover|watchdog|perst|link.{0,12}(?:down|train)|fatal', match[0], re.I):
            hits.append(dict(payload_offset=match.start(), file_offset=offset + match.start(),
                             text=match[0][:400].decode('ascii'), truncated=len(match[0]) > 400))
    version_offset, version_size = sec['.fwversion']
    return dict(file=path.name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                version=span(data, version_offset, version_size).rstrip(b'\0').decode('ascii'),
                payload_offset=offset, payload_size=size,
                signature_sections=[name for name in sec if name.startswith('.fwsignature')],
                embedded_riscv_elfs=images, rejected_elf_candidates=rejected,
                recovery_string_candidates=hits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='+', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    report = dict(schema=1, method='Static ELF container and bounded embedded-image inspection',
                  firmware_executed=False, hardware_accessed=False, recovery_verified=False,
                  caveats=['Strings are search anchors, not proven handlers or commands.',
                           'ELF extents are structural candidates, not decoded archive ownership.',
                           'Signature names do not prove runtime support or reset capability.'],
                  images=[inspect(path) for path in args.images])
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    for image in report['images']:
        print(image['file'], image['version'], 'ELFs:', len(image['embedded_riscv_elfs']),
              'recovery strings:', len(image['recovery_string_candidates']))

if __name__ == '__main__':
    main()
