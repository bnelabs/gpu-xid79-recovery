#!/usr/bin/env python3
"""Run a small fresh-context CUDA kernel through the driver API, without nvcc."""
import argparse
import ctypes as c
import json
import os
import re
import uuid

PTX=b'''.version 6.4
.target sm_75
.address_size 64
.visible .entry probe(.param .u64 buffer, .param .u32 count) {
 .reg .pred %p;
 .reg .b32 %r<6>;
 .reg .b64 %rd<4>;
 ld.param.u64 %rd1, [buffer];
 ld.param.u32 %r5, [count];
 mov.u32 %r1, %ctaid.x;
 mov.u32 %r2, %ntid.x;
 mov.u32 %r3, %tid.x;
 mad.lo.u32 %r1, %r1, %r2, %r3;
 setp.ge.u32 %p, %r1, %r5;
 @%p bra done;
 mul.wide.u32 %rd2, %r1, 4;
 add.u64 %rd3, %rd1, %rd2;
 ld.global.u32 %r4, [%rd3];
 add.u32 %r4, %r4, 7;
 st.global.u32 [%rd3], %r4;
done:
 ret;
}
'''


def probe(expected):
    # Set visibility before CUDA initialization, then independently verify UUID.
    os.environ['CUDA_VISIBLE_DEVICES']=expected
    driver=c.CDLL('libcuda.so.1')
    def api(name,args):
        function=getattr(driver,name);function.argtypes=args;function.restype=c.c_int
        def call(*values):
            status=function(*values)
            if status:raise RuntimeError(f'{name} failed with CUDA status {status}')
        return call
    ptr=c.c_void_p;uint=c.c_uint;integer=c.c_int;size=c.c_size_t;deviceptr=c.c_uint64
    init=api('cuInit',[uint]);count_api=api('cuDeviceGetCount',[c.POINTER(integer)])
    get_device=api('cuDeviceGet',[c.POINTER(integer),integer])
    get_uuid=api('cuDeviceGetUuid_v2',[ptr,integer])
    create=api('cuCtxCreate_v2',[c.POINTER(ptr),uint,integer])
    destroy=api('cuCtxDestroy_v2',[ptr]);allocate=api('cuMemAlloc_v2',[c.POINTER(deviceptr),size])
    free=api('cuMemFree_v2',[deviceptr]);h2d=api('cuMemcpyHtoD_v2',[deviceptr,ptr,size])
    d2h=api('cuMemcpyDtoH_v2',[ptr,deviceptr,size])
    load=api('cuModuleLoadData',[c.POINTER(ptr),ptr]);unload=api('cuModuleUnload',[ptr])
    get_function=api('cuModuleGetFunction',[c.POINTER(ptr),ptr,c.c_char_p])
    launch=api('cuLaunchKernel',[ptr,uint,uint,uint,uint,uint,uint,uint,ptr,c.POINTER(ptr),c.POINTER(ptr)])
    sync=api('cuCtxSynchronize',[])
    init(0);count=integer();count_api(c.byref(count))
    if count.value!=1:raise RuntimeError(f'Expected one UUID-selected CUDA device; got {count.value}')
    device=integer();get_device(c.byref(device),0);identifier=(c.c_ubyte*16)();get_uuid(identifier,device)
    actual='GPU-'+str(uuid.UUID(bytes=bytes(identifier)))
    if actual.lower()!=expected.lower():raise RuntimeError('CUDA returned a different GPU UUID')
    context=ptr();module=ptr();memory=deviceptr();create(c.byref(context),0,device)
    try:
        length=uint(1024);host=(uint*length.value)(*(i*13 for i in range(length.value)))
        allocate(c.byref(memory),c.sizeof(host));h2d(memory,c.cast(host,ptr),c.sizeof(host))
        image=c.create_string_buffer(PTX);load(c.byref(module),c.cast(image,ptr))
        function=ptr();get_function(c.byref(function),module,b'probe')
        parameters=(ptr*2)(c.cast(c.byref(memory),ptr),c.cast(c.byref(length),ptr))
        launch(function,4,1,1,256,1,1,0,None,parameters,None);sync()
        d2h(c.cast(host,ptr),memory,c.sizeof(host))
        if any(value!=i*13+7 for i,value in enumerate(host)):
            raise RuntimeError('CUDA kernel result mismatch')
    finally:
        # The caller bounds this whole process, including potentially stuck cleanup.
        try:
            if module:unload(module)
        finally:
            try:
                if memory.value:free(memory)
            finally:destroy(context)
    return {'uuid':actual,'elements_checked':1024,'fresh_context':True,
            'host_to_device':True,'kernel_execution':True,'device_to_host':True,'passed':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uuid',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',args.uuid):
        parser.error('A full physical GPU UUID is required')
    print(json.dumps(probe(args.uuid)),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'passed':False,'error':str(error)}),flush=True)
        raise SystemExit(1)
