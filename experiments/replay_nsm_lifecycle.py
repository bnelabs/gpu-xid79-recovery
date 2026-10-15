#!/usr/bin/env python3
"""Compile NVIDIA's exact async reset sequencer with modeled command outcomes.

No firmware is booted and no transport/hardware is accessed. The source method
is extracted unchanged after a pinned file hash check (Apache-2.0 upstream).
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile

COMMIT = 'b6cddce5ddab2afeed95142da608ed5865db96f2'
SOURCE = 'nsmd/nsmDbusIfaceOverride/nsmResetIface.hpp'
SHA256 = '71b28b189a2e541c29d7c1e00f74502874ae1fba2d06f0cf54eb4f9d643f8472'
PREFIX = r'''
#include <cassert>
#include <coroutine>
#include <cstdio>
#include <exception>
#include <memory>
#include <utility>
#include <vector>
namespace requester {
struct Coroutine {
 struct promise_type {
  int result = -1;
  Coroutine get_return_object() { return Coroutine{std::coroutine_handle<promise_type>::from_promise(*this)}; }
  std::suspend_never initial_suspend() noexcept { return {}; }
  std::suspend_always final_suspend() noexcept { return {}; }
  void return_value(int value) { result = value; }
  void unhandled_exception() { std::terminate(); }
 };
 std::coroutine_handle<promise_type> handle;
 explicit Coroutine(std::coroutine_handle<promise_type> h): handle(h) {}
 Coroutine(Coroutine&& other): handle(std::exchange(other.handle, {})) {}
 ~Coroutine() { if (handle) handle.destroy(); }
 bool await_ready() const noexcept { return true; }
 void await_suspend(std::coroutine_handle<>) const noexcept {}
 int await_resume() const noexcept { return handle.promise().result; }
};
}
namespace lg2 { template<typename... T> void error(T&&...) {} }
enum class AsyncOperationStatusType { Success, InternalFailure };
struct AsyncStatusIntf {
 AsyncOperationStatusType value = AsyncOperationStatusType::InternalFailure;
 int updates = 0;
 void status(AsyncOperationStatusType v) { value = v; updates++; }
};
// Values verified against libnsm/pci-links.h by the Python harness.
constexpr unsigned NOT_RESET = 0, RESET = 1, NSM_SW_SUCCESS = 0;
struct Sequencer {
 int assertion_result, release_result;
 std::vector<unsigned> calls;
 requester::Coroutine assertFundamentalReset(unsigned action) {
  calls.push_back(action);
  co_return action == RESET ? assertion_result : release_result;
 }
'''
SUFFIX = r'''
};
int main() {
 unsigned tests = 0, release_after_assertion_failure = 0;
 // Sweep every byte-sized assertion result, then every release result.
 for (unsigned pass = 0; pass < 2; pass++) for (unsigned code = 0; code < 256; code++) {
  Sequencer s{pass == 0 ? int(code) : 0, pass == 1 ? int(code) : 0, {}};
  auto status = std::make_shared<AsyncStatusIntf>();
  auto result = s.doResetOnDevice(status);
  assert(result.handle.done());
  // The coroutine return code is success even when the status reports failure.
  assert(result.handle.promise().result == NSM_SW_SUCCESS);
  assert(status->updates == 1);
  bool good = s.assertion_result == 0 && s.release_result == 0;
  assert(status->value == (good ? AsyncOperationStatusType::Success : AsyncOperationStatusType::InternalFailure));
  assert(s.calls[0] == RESET);
  if (s.assertion_result != 0) {
   if (s.calls.size() > 1) release_after_assertion_failure++;
   assert(s.calls.size() == 1);
  } else {
   assert(s.calls.size() == 2 && s.calls[1] == NOT_RESET);
  }
  tests++;
 }
 printf("{\"outcome_cases\":%u,\"status_checked\":true,\"return_code_always_success\":true,"
        "\"release_after_failed_assertion\":%u}\n", tests, release_after_assertion_failure);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    path = args.checkout/SOURCE
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != SHA256:
        raise RuntimeError('Unreviewed upstream source revision')
    # No parsing by model-generated line numbers: locate the exact class/method
    # and retain the complete body, including both awaits and the return code.
    source = blob.decode()
    start = source.index('    requester::Coroutine\n        doResetOnDevice', source.index('class NsmResetAsyncIntf'))
    opening = source.index('{', start)
    depth = 1
    end = opening+1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    method = source[start:end]
    pci = (args.checkout/'libnsm/pci-links.h').read_text()
    if 'enum action { NOT_RESET, RESET };' not in pci:
        raise RuntimeError('Unreviewed action values')
    with tempfile.TemporaryDirectory(prefix='nsm-sequencer-') as directory:
        base = pathlib.Path(directory)
        harness = base/'test.cpp'
        harness.write_text(PREFIX+method+SUFFIX)
        binary = base/'test'
        subprocess.run(['c++', '-std=c++20', '-O1', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(harness), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30, check=True)
        report = json.loads(result.stdout)
    report.update(kind='Unchanged upstream async sequencer with modeled command results',
        upstream_commit=COMMIT, source=SOURCE, source_sha256=SHA256,
        method_sha256=hashlib.sha256(method.encode()).hexdigest(), hardware_recovery_verified=False,
        limitation='No actual asynchronous transport scheduling, MCU firmware, CPLD, or GPU is emulated')
    value = json.dumps(report, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(value)
    print(value, end='')


if __name__ == '__main__':
    main()
