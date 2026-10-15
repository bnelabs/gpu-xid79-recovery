import struct,unittest
import firmware_image as f

class FirmwareImageTests(unittest.TestCase):
    def fixture(self):
        entries=[('.shstrtab',b'\0.shstrtab\0.fwimage\0.fwversion\0.fwsignature_ad10x\0.fwsignature_gb20x\0'),
                 ('.fwimage',b'example'),('.fwversion',b'123.45\0'),
                 ('.fwsignature_ad10x',b'a'*16),('.fwsignature_gb20x',b'b'*16)]
        data=bytearray(1024);data[:7]=b'\x7fELF\x02\x01\x01'
        struct.pack_into('<H',data,18,243);struct.pack_into('<Q',data,40,64)
        struct.pack_into('<HHH',data,58,64,6,1)
        pos=512
        for i,(name,content) in enumerate(entries,1):
            struct.pack_into('<IIQQQQIIQQ',data,64+i*64,entries[0][1].index(name.encode()),1,0,0,pos,len(content),0,0,1,0)
            data[pos:pos+len(content)]=content;pos+=len(content)
        return data
    def test_reports_version_and_family_sections_without_claiming_support(self):
        result=f.inspect_image(self.fixture(),'123.45')
        self.assertTrue(result['version_matches_driver'])
        self.assertEqual(result['signature_sections'],['ad10x','gb20x'])
        self.assertFalse(f.inspect_image(self.fixture(),'other')['version_matches_driver'])
        self.assertIsNone(f.inspect_image(self.fixture())['version_matches_driver'])
    def test_truncated_and_overflowing_tables_and_sections_are_refused(self):
        data=self.fixture()
        for blob in (data[:63],data[:300]):
            with self.assertRaises(ValueError):f.inspect_image(blob)
        struct.pack_into('<Q',data,64+2*64+32,2**64-1)
        with self.assertRaises(ValueError):f.inspect_image(data)
    def test_duplicate_and_unterminated_names_are_refused(self):
        data=self.fixture();name=struct.unpack_from('<I',data,64+2*64)[0]
        struct.pack_into('<I',data,64+3*64,name)
        with self.assertRaisesRegex(ValueError,'Duplicate'):f.inspect_image(data)
        data=self.fixture();size=struct.unpack_from('<Q',data,64+64+32)[0]
        data[512:512+size]=b'x'*size
        with self.assertRaisesRegex(ValueError,'Invalid section name'):f.inspect_image(data)
