#!/usr/bin/env python3
"""Read NVIDIA's public certification indexes and report breadth, not reset support."""
import argparse
import collections
import datetime
import hashlib
from html.parser import HTMLParser
import json
import urllib.request

URLS = [f'https://docs.nvidia.com/certification-programs/latest/{name}.html' for name in
        ('nvidia-certified-systems', 'nvidia-certified-systems-previous-generation')]


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.table = []
        if self.table is not None and tag == 'tr': self.row = []
        if self.row is not None and tag in ('td', 'th'): self.cell = []

    def handle_data(self, value):
        if self.cell is not None: self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        if tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    reports = []
    for url in URLS:
        with urllib.request.urlopen(url, timeout=30) as response:
            if response.geturl() != url: raise RuntimeError('Unexpected source redirect')
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024: raise RuntimeError('Index exceeds size bound')
        parsed = Tables(); parsed.feed(data.decode('utf-8'))
        tables = []
        partners = collections.Counter()
        for table in parsed.tables:
            if not table or not table[0] or table[0][0] != 'Partner': continue
            header, rows = table[0], table[1:]
            category = ('data_center_general' if header == ['Partner', 'Data Center Server', 'Supported NVIDIA GPUs']
                        else 'other_certification_or_reference_configuration')
            tables.append({'table_index': len(tables), 'category': category,
                           'column_count': len(header), 'published_rows': len(rows)})
            for row in rows:
                if row and row[0]: partners[row[0]] += 1
        if not tables: raise RuntimeError('No recognized certification tables; review source schema')
        reports.append({'url': url, 'html_sha256': hashlib.sha256(data).hexdigest(),
                        'tables': tables, 'partner_labels': len(partners),
                        'published_rows': sum(t['published_rows'] for t in tables)})
    report = {'schema': 1, 'retrieved_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'sources': reports, 'recovery_qualified': False,
        'limitations': ['Rows overlap between certification categories; not unique systems or valid CPU/GPU combinations',
                       'Counts include workstation, edge, VDI and reference configuration categories',
                       'No GPU resets, BIOS combinations or actual machine state validated']}
    with open(args.output, 'w') as stream: json.dump(report, stream, indent=2); stream.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
