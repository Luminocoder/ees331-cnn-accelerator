"""Freeze the exact PL weights, bias32, twenty raw inputs and golden logits."""
import hashlib
import json
from pathlib import Path
import struct
import argparse
import subprocess

ROOT=Path(__file__).resolve().parent.parent
ARM=ROOT/'hardware/arm'

def tokens(path): return bytes(int(x,16) for x in path.read_text().split())
def array(name, kind, values, per=24):
    return ('const '+kind+' '+name+'['+str(len(values))+'] = {\n'+
        '\n'.join(','.join(str(x) for x in values[i:i+per])+',' for i in range(0,len(values),per))+'\n};\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',default='C:/Program Files/LLVM/bin/clang.exe')
    args=parser.parse_args()
    compiler_version=subprocess.check_output([args.compiler,'--version'],text=True).splitlines()[0]
    weights=ROOT/'hardware/pl/ref_model/weights_int8'
    frozen=ROOT/'resources/samples/_frozen'
    names=('Conv1','Conv2','FC1','FC2'); sizes=(72,1152,50176,640); counts=(8,16,64,10)
    bias_bytes=tokens(weights/'bias_layout.txt')
    # PL bias_layout stores each signed int32 MSB first; padding may follow.
    bias=list(struct.unpack('>'+str(len(bias_bytes)//4)+'i',bias_bytes))
    header=['#ifndef BENCHMARK_DATA_H\n#define BENCHMARK_DATA_H\n#include <stdint.h>\n']
    source=['#include "benchmark_data.h"\n']; model=b''; offset=0
    def emit(name,kind,vals):
        header.append('extern const '+kind+' '+name+'['+str(len(vals))+'];\n')
        source.append(array(name,kind,vals))
    for name,size,count,nin in zip(names,sizes,counts,(9,72,784,64)):
        raw=tokens(weights/(name+'_int8.txt')); assert len(raw)==size
        # Verify the constants loaded into PL equal the host reference weights.
        assert raw==tokens(ROOT/'resources/weights_int8'/(name+'_int8.txt'))
        b=bias[offset:offset+count]; offset+=count
        assert len(b)==count and max(abs(x) for x in b)+nin*127*128<2**31
        emit('bench_w_'+name,'int8_t',[v-256 if v>=128 else v for v in raw])
        emit('bench_b_'+name,'int32_t',b)
        model+=raw+struct.pack('<'+str(count)+'i',*b)
    model+=struct.pack('<3I',9,9,10)
    inputs=b''.join(tokens(frozen/('img_%02d.txt'%i)) for i in range(20))
    assert len(inputs)==20*784
    gold=[int(x) for x in (frozen/'expected_scores.txt').read_text().split()]
    assert len(gold)==200
    goldbytes=struct.pack('<200i',*gold)
    emit('bench_images','uint8_t',list(inputs)); emit('bench_expected','int32_t',gold)
    flags='clang ARMv7a Cortex-A9 ARM softfloat mfpu=none no-unaligned-access -O2 no-LTO'
    implementation=hashlib.sha256((flags+compiler_version).encode()+b''.join((ARM/f).read_bytes() for f in
        ('cpu_infer.c','cpu_infer.h','bench_platform.c','bench_platform.h','benchmark.c','benchmark.h',
         'fw_seg.c','runtime.c','start.S','link.ld'))).hexdigest()
    hashes=[hashlib.sha256(x).hexdigest() for x in (model,inputs,goldbytes)]+[implementation]
    for name,value in zip(('model','dataset','golden','implementation'),hashes):
        emit('bench_hash_'+name,'uint8_t',list(bytes.fromhex(value)))
    header.append('#endif\n')
    (ARM/'benchmark_data.h').write_text(''.join(header),encoding='utf-8')
    (ARM/'benchmark_data.c').write_text(''.join(source),encoding='utf-8')
    metadata=dict(protocol='BNC1',hashes=dict(zip(('model','dataset','golden','implementation'),hashes)),
        compiler_flags=flags,compiler_version=compiler_version,neon=False,scalar_single_core=True,samples=20,
        input_crc32=[],expected=[gold[i*10:(i+1)*10] for i in range(20)],
        scope='raw uint8 input in ARM memory to 10 int32 logits in ARM memory; includes AXI transfers',
        cache='L1 I/D enabled, 1MiB identity normal inner-WBWA outer-NC nonshareable; MMIO Strongly Ordered; restored after test',
        reference_clock_hz=33333333)
    import zlib
    metadata['input_crc32']=[zlib.crc32(inputs[i*784:(i+1)*784])&0xffffffff for i in range(20)]
    dest=ROOT/'resources/benchmark'; dest.mkdir(exist_ok=True)
    (dest/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    print('BENCHMARK_DATA_GENERATED',hashes[0])

if __name__=='__main__': main()
