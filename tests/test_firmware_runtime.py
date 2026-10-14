import unittest
import firmware_runtime as f

class FirmwareRuntimeTests(unittest.TestCase):
    def test_overlay_views_do_not_hide_the_complete_nvs_layout(self):
        source='OperationRegion (GNVS, SystemMemory, 0x1000, 0x08) Field (GNVS, AnyAcc, Lock, Preserve) { P1RE, 32, P1RP, 8, ,24 } Field (GNVS, ByteAcc, NoLock, Preserve) { AAAA, 8 }'
        self.assertEqual(f.nvs_definition(source)[2]['P1RP'],(32,8))
        with self.assertRaises(ValueError):f.nvs_definition(source+' Field (GNVS, AnyAcc, Lock, Preserve) { BBBB, 64 }')
    def test_fields_include_reserved_bits_and_explicit_offsets(self):
        fields,bits=f.field_layout('AAAA, 8, , 8, BBBB, 16, Offset (0x08), CCCC, 32')
        self.assertEqual(fields,{'AAAA':(0,8),'BBBB':(16,16),'CCCC':(64,32)})
        self.assertEqual(bits,96)
    def test_ambiguous_or_unsupported_layout_refused(self):
        for source in ('AAAA, 8, Offset (Zero), BBBB, 8','AccessAs (ByteAcc, Zero), AAAA, 8','AAAA, 8, AAAA, 8'):
            with self.assertRaises(ValueError):f.field_layout(source)
    def test_nvs_region_requires_exact_layout_and_reserved_resource(self):
        base,size,fields=f.nvs_definition('OperationRegion (GNVS, SystemMemory, 0x1000, 0x04) Field (GNVS, AnyAcc, Lock, Preserve) { P1RE, 32 }')
        self.assertEqual((base,size),(4096,4))
        self.assertTrue(f.nvs_resource(base,size,'1000-1fff : ACPI Non-volatile Storage'))
        self.assertFalse(f.nvs_resource(base,size,'1000-1fff : System RAM'))
        with self.assertRaises(ValueError):f.nvs_definition('OperationRegion (GNVS, SystemMemory, 0x1000, 0x08) Field (GNVS, AnyAcc, Lock, Preserve) { P1RE, 32 }')
    def test_gpio_offset_comes_from_live_range_not_a_fixed_line(self):
        reference={'family':'Test SoC','acpi_hids':['INTC1056'],'pad_prefix':0x08000000,
                   'groups':{'14':{'name':'GPP_E','physical_start':208,'count':23}}}
        ranges='64: INTC1056:00 GPIOS [964 - 986] PINS [208 - 230]'
        mapping=f.gpio_mapping(0x080e0002,reference,'INTC1056:00',ranges)
        self.assertEqual(mapping['gpio_line_offset'],66)
        self.assertEqual(mapping['physical_pin'],210)
        self.assertIsNone(f.gpio_mapping(0x080e0002,reference,'UNKNOWN:00',ranges))
        self.assertIsNone(f.gpio_mapping(0x080e0020,reference,'INTC1056:00',ranges))
        self.assertIsNone(f.gpio_mapping(0x080e0002,reference,'INTC1056:00',ranges.replace('230','229')))
