import pathlib,struct,tempfile,unittest
import firmware_policy as f

class FirmwarePolicyTests(unittest.TestCase):
    def metadata(self,entries):
        return struct.pack('<QQQ',64,40,len(entries))+b''.join(struct.pack('<H',token)+name.encode().ljust(64,b'\0')+b'gBoardTokens'.ljust(40,b'\0') for token,name in entries)
    def executable(self,code,machine=0x14c):
        data=bytearray(1024);struct.pack_into('<HHBBH',data,0,0x5a56,machine,1,11,40)
        struct.pack_into('<8sIIII',data,40,b'.text',128,256,128,256)
        struct.pack_into('<I',data,76,0x20);data[256:256+len(code)]=code
        data[768:784]=f.PCD_GUID
        return bytes(data)
    def test_metadata_recovers_firmware_local_names(self):
        result=f.names_from_metadata(self.metadata([(0x91,'PcdPegSlot4RstGpioNo'),(2,'AmiOtherToken')]))
        self.assertEqual(result[0x91]['name'],'PcdPegSlot4RstGpioNo')
        self.assertIn(2,result)
    def test_malformed_and_duplicate_metadata_refused(self):
        for data in (b'\xff'*100,self.metadata([(1,'PcdA')])[:-1],self.metadata([(1,'PcdA'),(1,'PcdB')])):
            self.assertEqual(f.names_from_metadata(data),{})
    def test_extracts_assignment_without_any_known_board_or_pin(self):
        names=f.names_from_metadata(self.metadata([(0x91,'PcdPegSlot4RstGpioNo')]))
        code=b'\x68'+struct.pack('<I',0x08230007)+b'\x68'+struct.pack('<I',0x91)+b'\xff\x50\x44'
        writes=f.policy_writes(self.executable(code),names)
        self.assertEqual(writes[0]['value'],0x08230007)
        self.assertEqual(writes[0]['name'],'PcdPegSlot4RstGpioNo')
        self.assertEqual(writes[0]['file_offset'],256)
    def test_register_value_stays_unknown(self):
        code=b'\x55\x68\xff\x00\x00\x00\xff\x50\x50'
        writes=f.policy_writes(self.executable(code),{255:{'name':'PcdPegSlot1RstGpioPolarity','namespace':'gBoard'}})
        self.assertIsNone(writes[0]['value']);self.assertEqual(writes[0]['value_register'],5)
    def test_data_sections_and_other_architectures_are_not_decoded(self):
        code=b'\x68\x02\x00\x0e\x08\x68\x91\x00\x00\x00\xff\x50\x44'
        names={0x91:{'name':'PcdPegSlot1RstGpioNo','namespace':'gBoard'}}
        data=bytearray(self.executable(code));struct.pack_into('<I',data,76,0x40)
        self.assertEqual(f.policy_writes(bytes(data),names),[])
        self.assertEqual(f.policy_writes(self.executable(code,0x8664),names),[])
    def test_x64_named_getter_copy_correlates_by_offset_and_width(self):
        names={0x91:{'name':'PcdPegSlot4RstGpioNo','namespace':'gBoard'}}
        code=b'\xe8\x00\x00\x00\x00\xb9'+struct.pack('<I',0x91)+b'\xff\x50\x18\x48\x8b\x0d\x00\x00\x00\x00\x89\x81'+struct.pack('<I',1234)
        data=bytearray(self.executable(code,0x8664));data[768:784]=f.PCD_PROTOCOL_GUID
        copies=f.policy_copies(bytes(data),names)
        self.assertEqual(len(copies),1)
        self.assertEqual((copies[0]['destination_byte_offset'],copies[0]['width']),(1234,32))
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for name,blob in (('metadata',self.metadata([(0x91,'PcdPegSlot4RstGpioNo')])),('x64',bytes(data))):
                (root/name).mkdir();(root/name/'body.bin').write_bytes(blob)
            self.assertEqual(len(f.scan(root)['databases'][0]['runtime_field_copies']),1)
        report={'databases':[{'metadata_sha256':'example','runtime_field_copies':copies}]}
        fields={'ABCD':{'bit_offset':1234*8,'width':32,'value':123},'EFGH':{'bit_offset':1234*8,'width':8,'value':1}}
        joined=f.correlate_runtime(report,{'fields':fields})
        self.assertEqual([(c['acpi_field'],c['live_value']) for c in joined],[('ABCD',123)])
        self.assertEqual(f.policy_copies(self.executable(code,0x8664),names),[])
        bad=bytearray(data);bad[276]=0x88
        self.assertEqual(f.policy_copies(bytes(bad),names),[])
        struct.pack_into('<I',data,76,0x40)
        self.assertEqual(f.policy_copies(bytes(data),names),[])
