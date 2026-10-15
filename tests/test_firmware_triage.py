"""Malformed-container checks for the offline firmware inventory reader."""
import importlib.util
import pathlib
import struct
import unittest

spec = importlib.util.spec_from_file_location('firmware_triage', pathlib.Path(__file__).parents[1] / 'experiments/inspect_gsp_firmware.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FirmwareTriageTests(unittest.TestCase):
    def test_range_checks(self):
        for offset, size in [(-1, 1), (0, -1), (4, 0), (1, 3), (2**64, 1)]:
            with self.assertRaises(ValueError):
                m.span(b'abc', offset, size)
        self.assertEqual(m.span(b'abc', 3, 0), b'')

    def test_wrong_format(self):
        for data in [b'', b'\x7fELF', b'\x7fELF\x01\x01\x01' + bytes(64)]:
            with self.assertRaises(ValueError):
                m.header(data)

    def image(self, file_size=4):
        data = bytearray(128)
        data[:7] = b'\x7fELF\x02\x01\x01'
        struct.pack_into('<HHIQQQIHHHHHH', data, 16,
                         2, 243, 1, 0x1000, 64, 0, 1, 64, 56, 1, 64, 0, 0)
        struct.pack_into('<IIQQQQQQ', data, 64, 1, 5, 120, 0x1000, 0x1000, file_size, file_size, 4)
        return data

    def test_candidate_extent_and_unresolved_mapping(self):
        images, rejected = m.embedded(bytes(self.image()))
        self.assertEqual(rejected, 0)
        self.assertEqual(images[0]['size'], 124)
        self.assertEqual(images[0]['architecture_mapping'], 'unresolved')
        self.assertFalse(images[0]['section_table_valid'])

    def test_program_range_outside_input(self):
        self.assertEqual(m.embedded(bytes(self.image(1000))), ([], 1))

    def test_stale_section_table_does_not_authorize_mapping(self):
        data = self.image()
        struct.pack_into('<Q', data, 40, 2**63)
        struct.pack_into('<H', data, 60, 1)
        images, rejected = m.embedded(bytes(data))
        self.assertEqual(rejected, 0)
        self.assertFalse(images[0]['section_table_valid'])
        self.assertEqual(images[0]['size'], 124)
