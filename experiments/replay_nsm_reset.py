#!/usr/bin/env python3
"""Exercise NVIDIA's NSM reset codec offline; never opens a transport or GPU."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile

EXPECTED = {
    'base.c': 'dc74a70cde9e523f7074ac7f6bdb7eac45573c882555dc72baa314b6abdb0e2b',
    'base.h': '83652c77ea29e032ae1913a73c1c02a4bb4303336f277679a32a239f3ac8c645',
    'pci-links.c': 'f3952621036adad6e515bf082bac1c28b5360053c6af721e89da382ab00220a2',
    'pci-links.h': '3359cd97741567a86a559715643937d973e61713cc92cb16e3db0c191153e179',
    'device-capability-discovery.h': '9b7b58f86547be70c357ae97feae29b044e2cf145d55d4b2c0e91bb522ff2e19',
}
HARNESS = r'''
#include "base.h"
#include "pci-links.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
 unsigned char buffer[256] = {0};
 struct nsm_msg *msg = (struct nsm_msg *)buffer;
 size_t req_len = sizeof(struct nsm_msg_hdr) + sizeof(struct nsm_assert_pcie_fundamental_reset_req);
 unsigned roundtrips = 0;
 for (int index = 0; index < 256; index++) for (int action = 0; action < 2; action++) {
  uint8_t decoded_index = 0, decoded_action = 0;
  assert(encode_assert_pcie_fundamental_reset_req(7, index, action, msg) == NSM_SW_SUCCESS);
  assert(msg->hdr.nvidia_msg_type == NSM_TYPE_PCI_LINK);
  assert(msg->hdr.instance_id == 7 && msg->hdr.request);
  assert(msg->payload[0] == 0x60 && msg->payload[1] == 2);
  assert(decode_assert_pcie_fundamental_reset_req(msg, req_len, &decoded_index, &decoded_action) == NSM_SW_SUCCESS);
  assert(decoded_index == index && decoded_action == action);
  roundtrips++;
 }
 uint8_t index = 0, action = 0;
 unsigned rejected_lengths = 0;
 for (size_t size = 0; size < req_len; size++) {
  assert(decode_assert_pcie_fundamental_reset_req(msg, size, &index, &action) != NSM_SW_SUCCESS);
  rejected_lengths++;
 }
 assert(decode_assert_pcie_fundamental_reset_req(NULL, req_len, &index, &action) != NSM_SW_SUCCESS);
 uint8_t cc = 0; uint16_t data_size = 0, reason = 0;
 size_t resp_len = sizeof(struct nsm_msg_hdr) + sizeof(struct nsm_common_resp);
 assert(encode_assert_pcie_fundamental_reset_resp(7, NSM_SUCCESS, 0, msg) == NSM_SW_SUCCESS);
 assert(decode_assert_pcie_fundamental_reset_resp(msg, resp_len, &cc, &data_size, &reason) == NSM_SW_SUCCESS);
 assert(cc == NSM_SUCCESS && data_size == 0);
 assert(encode_assert_pcie_fundamental_reset_resp(7, NSM_ERR_UNSUPPORTED_COMMAND_CODE, 0x1234, msg) == NSM_SW_SUCCESS);
 resp_len = sizeof(struct nsm_msg_hdr) + sizeof(struct nsm_common_non_success_resp);
 assert(decode_assert_pcie_fundamental_reset_resp(msg, resp_len, &cc, &data_size, &reason) == NSM_SW_SUCCESS);
 assert(cc == NSM_ERR_UNSUPPORTED_COMMAND_CODE && reason == 0x1234);
 bitfield8_t supported[SUPPORTED_COMMAND_CODE_DATA_SIZE] = {0}, decoded[SUPPORTED_COMMAND_CODE_DATA_SIZE] = {0};
 supported[0x60 / 8].byte = 1U << (0x60 % 8);
 assert(encode_get_supported_command_codes_resp(7, NSM_SUCCESS, 0, supported, msg) == NSM_SW_SUCCESS);
 assert(decode_get_supported_command_codes_resp(msg, sizeof(struct nsm_msg_hdr) + sizeof(struct nsm_get_supported_command_codes_resp), &cc, &reason, decoded) == NSM_SW_SUCCESS);
 assert(cc == NSM_SUCCESS && (decoded[0x60 / 8].byte & (1U << (0x60 % 8))));
 /* Record, rather than hide, the codec's lack of action semantic validation. */
 int invalid_action_accepted = encode_assert_pcie_fundamental_reset_req(7, 0, 255, msg) == NSM_SW_SUCCESS;
 printf("{\"request_roundtrips\":%u,\"truncated_requests_rejected\":%u,\"success_response\":true,\"unsupported_command_response\":true,\"capability_bitmap\":true,\"codec_accepts_out_of_range_action\":%s}\n",
        roundtrips, rejected_lengths, invalid_action_accepted ? "true" : "false");
 return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=pathlib.Path, help='NVIDIA/nsmd checkout')
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    source = args.checkout.resolve() / 'libnsm'
    for name, expected in EXPECTED.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Unreviewed upstream input: {name}')
    with tempfile.TemporaryDirectory(prefix='nsm-reset-replay-') as directory:
        base = pathlib.Path(directory)
        harness = base / 'test.c'
        harness.write_text(HARNESS)
        binary = base / 'test'
        subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', '-ffunction-sections', '-fdata-sections',
                        '-I', str(source), str(harness), str(source / 'base.c'),
                        str(source / 'pci-links.c'), '-Wl,--gc-sections', '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr or f'Replay exited {result.returncode}')
        report = json.loads(result.stdout)
    report.update({'kind': 'Offline upstream NSM codec replay; no transport or hardware access',
                   'upstream_commit': 'b6cddce5ddab2afeed95142da608ed5865db96f2',
                   'input_sha256': EXPECTED, 'hardware_recovery_verified': False})
    output = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    main()
