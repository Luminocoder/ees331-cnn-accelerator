"""No-camera paired Cortex-A9/PL measurement, using board global-timer ticks.

No PC inference timing enters the result. Failed or emulated runs never publish
a hardware speedup. Every measured output is checked against frozen gold logits.
"""
import csv
import hashlib
import json
from pathlib import Path
import secrets
import statistics
import struct
import time
import uuid

import mnist_serial as wire

INFO,CONTROL,RUN=7,8,9
KEYS=('samples','profile','optimization','neon','active','sctlr','actlr','l2_control',
      'timer_control','arm_pll','ddr_pll','io_pll','arm_clock','fpga0_clock','clock_621',
      'reference_hz','mpidr','midr','timer_overhead_ticks','boot_mode','scu_control')
HASHES=('model','dataset','golden','implementation')

def decode_info(data):
    if len(data)!=224 or data[:4]!=b'BNC1': raise ValueError('不支持的性能测试固件；请用v5.4重新初始化')
    result=dict(zip(KEYS,struct.unpack_from('<21I',data,132)))
    result['hashes']={name:data[4+i*32:36+i*32].hex() for i,name in enumerate(HASHES)}
    result['counter_ticks']=struct.unpack_from('<Q',data,216)[0]
    return result

def clock_rates(info):
    def pll(name):
        value=info[name]
        if value&3: raise ValueError('PLL处于复位/掉电状态')
        bypass=bool(value&16) or bool(value&8 and info['boot_mode']&16)
        factor=1 if bypass else (value>>12)&127
        if not factor: raise ValueError('PLL倍频值无效')
        return info['reference_hz']*factor
    arm=info['arm_clock']; div=(arm>>8)&63; source=(arm>>4)&3
    if not div or not arm&(1<<24): raise ValueError('ARM时钟未启用或分频无效')
    cpu=pll(('arm_pll','arm_pll','ddr_pll','io_pll')[source])/div
    f=info['fpga0_clock']; d0=(f>>8)&63; d1=(f>>20)&63
    if not d0 or not d1: raise ValueError('PL时钟分频无效')
    fpga=pll(('io_pll','io_pll','arm_pll','ddr_pll')[(f>>4)&3])/d0/d1
    return {'arm_hz':cpu,'pl_hz':fpga,
            'timer_hz':cpu/2/(((info['timer_control']>>8)&255)+1),
            'frequency_source':'SLCR readback + configured 33.333333 MHz reference; host elapsed-time cross-check'}

class Trace:
    def __init__(self,stream,log): self.stream=stream; self.log=log
    def exchange(self,kind,payload=b'',timeout=20):
        seq=secrets.randbits(32); request=wire.encode_packet(kind,seq,payload)
        tx=bytearray(); rx=bytearray(); stream=self.stream
        class Tap:
            def write(self,data):
                n=stream.write(data)
                if n: tx.extend(data[:n])
                return n
            def read(self,n):
                data=stream.read(n); rx.extend(data); return data
        start=time.monotonic()
        try: return wire._exchange(Tap(),request,kind|0x80,seq,timeout)
        finally:
            self.log.write(json.dumps({'kind':kind,'sequence':seq,'host_elapsed_seconds':time.monotonic()-start,
                'tx_hex':tx.hex(),'rx_hex':rx.hex()})+'\n'); self.log.flush()
    def info(self): return decode_info(self.exchange(INFO))

def verify_identity(info,metadata):
    if info['hashes']!=metadata['hashes']: raise ValueError('运行固件的模型/输入/金标准/实现哈希与本包不符')
    if (info['samples'],info['profile'],info['optimization'],info['neon'])!=(20,1,2,0):
        raise ValueError('不支持的测试配置')
    if info['mpidr']&255: raise ValueError('测试必须在ARM CPU0单核执行')

def validate_row(data,index,order,metadata,active):
    if len(data)!=132 or data[:4]!=b'BNR1': raise ValueError('性能测试回包长度/版本错误')
    fields=struct.unpack_from('<8I2Q20i',data,4)
    names=('index','order','status','cpu_match_mask','pl_match_mask','input_crc32','sctlr','timer_control','cpu_ticks','pl_ticks')
    row=dict(zip(names,fields[:10])); row['cpu_logits']=list(fields[10:20]); row['pl_logits']=list(fields[20:30])
    if (row['index'],row['order'])!=(index,order): raise ValueError('样本序号或执行顺序不符')
    if row['input_crc32']!=metadata['input_crc32'][index]: raise ValueError('板端输入CRC不符')
    if row['status'] or row['cpu_match_mask']!=1023 or row['pl_match_mask']!=1023:
        raise ValueError('板端分数/加速器状态校验失败')
    if row['cpu_logits']!=metadata['expected'][index] or row['pl_logits']!=metadata['expected'][index]:
        raise ValueError('ARM、PL和金标准10路分数不一致')
    if row['sctlr']!=active['sctlr'] or row['timer_control']!=active['timer_control']:
        raise ValueError('测试中缓存或计时器配置变化')
    if min(row['cpu_ticks'],row['pl_ticks'])<=max(1,active['timer_overhead_ticks']*10):
        raise ValueError('计时器未运行或分辨率不足')
    return row

def calibrate(trace,active,rates):
    records=[]
    for _ in range(3):
        a0=time.monotonic(); a=trace.info(); a1=time.monotonic()
        time.sleep(0.25)
        b0=time.monotonic(); b=trace.info(); b1=time.monotonic()
        dt=(b0+b1-a0-a1)/2
        measured=(b['counter_ticks']-a['counter_ticks'])/dt
        records.append({'host_seconds':dt,'delta_ticks':b['counter_ticks']-a['counter_ticks'],
                        'measured_hz':measured,'relative_error':abs(measured/rates['timer_hz']-1),
                        'max_uart_query_seconds':max(a1-a0,b1-b0)})
        for snapshot in (a,b):
            for key in ('sctlr','timer_control','arm_pll','ddr_pll','io_pll','arm_clock','fpga0_clock','clock_621'):
                if snapshot[key]!=active[key]: raise ValueError('校准期间板端时钟或缓存变化')
    if any(r['relative_error']>0.08 for r in records): raise ValueError('计时频率与主机时间交叉检查不符，拒绝生成绝对耗时和加速比')
    return records

def summary(rows,hz):
    result={}
    for name in ('cpu','pl'):
        ticks=[r[name+'_ticks'] for r in rows]; values=sorted(t/hz*1000 for t in ticks)
        result[name]={'count':len(values),'total_ticks':sum(ticks),'mean_ms':statistics.mean(values),
                      'median_ms':statistics.median(values),'p95_ms':values[(95*len(values)+99)//100-1],
                      'min_ms':values[0],'max_ms':values[-1]}
    result['speedup']=result['cpu']['total_ticks']/result['pl']['total_ticks']
    result['formula']='sum(cpu_ticks) / sum(pl_ticks), same 100 paired inferences'
    return result

def run_on_stream(stream,resources,output_root,progress=lambda s:None,cancel=lambda:False,
                  evidence_kind='board_uart'):
    """Injected streams are for tests only; the app never exposes an emulation flag."""
    directory=Path(output_root)/(time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    metadata_path=Path(resources)/'benchmark/metadata.json'
    metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
    report={'status':'RUNNING','camera_opened':False,'initialization_executed':False,
            'evidence_kind':evidence_kind,'hardware_accessed':evidence_kind=='board_uart',
            'valid_for_hardware_report':False,'speedup':None,'rows':[],
            'conditions':metadata,'metadata_file_sha256':hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            'warmup_pairs':10,'validation_pairs':20,'formal_pairs':100,
            'timer_overhead_subtracted':False,
            'scope':metadata['scope'],'excludes':['camera','preprocessing','UART','logs','display','initialization','weight loading'],
            'baseline_limit':'single-core scalar C -O2; NEON disabled; not a claim against the fastest possible ARM implementation'}
    save=lambda:(directory/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    started=False; before=None; active=None
    with (directory/'wire.jsonl').open('w',encoding='utf-8') as wirelog, (directory/'rows.csv').open('w',encoding='utf-8-sig',newline='') as csvfile:
        trace=Trace(stream,wirelog)
        columns=['phase','round','index','order','cpu_ticks','pl_ticks','cpu_ms','pl_ms','input_crc32','cpu_logits','pl_logits']
        writer=csv.DictWriter(csvfile,fieldnames=columns); writer.writeheader()
        try:
            progress('核对固件、模型和固定输入…')
            before=trace.info(); report['before']=before; verify_identity(before,metadata)
            if before['active']: raise ValueError('板端已有未结束的性能测试，请重新初始化后再测')
            started=True
            active=decode_info(trace.exchange(CONTROL,b'\x01')); report['active']=active
            verify_identity(active,metadata)
            if not active['active'] or active['sctlr']&0x1005!=0x1005 or not active['timer_control']&1:
                raise ValueError('L1缓存/MMU/硬件计时器没有按要求启用')
            rates=clock_rates(active); report['clocks']=rates
            if evidence_kind=='board_uart': report['timebase_crosscheck']=calibrate(trace,active,rates)
            else: report['timebase_crosscheck']='NOT_HARDWARE: deliberately skipped for firmware emulator'
            for phase,count in (('validation',20),('warmup',10),('formal',100)):
                for n in range(count):
                    if cancel(): raise InterruptedError('用户已停止测试；不生成加速比')
                    index=n%20; order=(n//20+index)%2
                    raw=trace.exchange(RUN,bytes([index,order]))
                    # Preserve failed raw replies too, before interpreting them.
                    (directory/'last_reply.hex').write_text(raw.hex()+'\n',encoding='ascii')
                    row=validate_row(raw,index,order,metadata,active)
                    row.update(phase=phase,round=n//20,cpu_ms=row['cpu_ticks']/rates['timer_hz']*1000,
                               pl_ms=row['pl_ticks']/rates['timer_hz']*1000)
                    report['rows'].append(row)
                    writer.writerow({k:json.dumps(row[k]) if k.endswith('logits') else row[k] for k in columns}); csvfile.flush()
                    if n%5==0 or n+1==count: progress('%s %d/%d：ARM/PL各10路分数一致'%(phase,n+1,count))
                    save()
            after=trace.info(); report['after']=after
            verify_identity(after,metadata)
            for key in KEYS:
                if after[key]!=active[key]: raise ValueError('测量期间板端环境发生变化：'+key)
            report['status']='MEASUREMENTS_COMPLETE_PENDING_RESTORE'
        except InterruptedError as exc: report.update(status='CANCELLED',error=str(exc))
        except Exception as exc: report.update(status='FAILED',error=str(exc))
        finally:
            if started:
                try:
                    restored=decode_info(trace.exchange(CONTROL,b'\x00')); report['restored']=restored
                    if restored['active'] or restored['sctlr']!=before['sctlr'] or restored['timer_control']!=before['timer_control']:
                        raise ValueError('测试结束后缓存/计时器原配置未恢复')
                except Exception as exc: report.update(status='FAILED',restore_error=str(exc))
            if report['status']=='MEASUREMENTS_COMPLETE_PENDING_RESTORE':
                formal=[r for r in report['rows'] if r['phase']=='formal']; assert len(formal)==100
                if evidence_kind=='board_uart':
                    report['statistics']=summary(formal,report['clocks']['timer_hz'])
                    report.update(status='BOARD_BENCHMARK_PASS',valid_for_hardware_report=True,
                                  speedup=report['statistics']['speedup'])
                else: report['status']='EMULATED_FLOW_PASS_NOT_A_MEASUREMENT'
            save()
    if report['valid_for_hardware_report']:
        s=report['statistics']
        text=('ARM单核标量C -O2（L1开启，无NEON）与ARM调用PL；20张×5轮，100次/路径。\n'
              'ARM均值 %.6f ms；ARM+PL均值 %.6f ms；加速比 %.4f×。\n'
              'PL时间包含量化、AXI传图、启动、等待、结果读回。全部分数逐项校验通过。\n'
              '结果仅适用于记录的缓存、频率、编译和稳态条件。\n')%(s['cpu']['mean_ms'],s['pl']['mean_ms'],report['speedup'])
        if report['speedup']<1:
            text+='该基线下ARM+PL耗时为ARM的%.4f倍，未取得性能加速；通过状态表示校验和测量有效。\n'%(1/report['speedup'])
    else: text='本次没有可用于报告的实板加速比。状态：'+report['status']+'\n'+report.get('error',report.get('restore_error','模拟流程只验证软件逻辑'))+'\n'
    (directory/'结论.md').write_text(text,encoding='utf-8')
    return report,directory

def run_cli(board,port,resources,output_root):
    try:
        board.connect(port); board.hello()
        report,directory=run_on_stream(board.stream,resources,output_root,progress=lambda s:print(s,flush=True))
        print('BENCHMARK_LOG='+str(directory)); print(report['status'])
        if report['speedup'] is not None: print('SPEEDUP=%.6fx'%report['speedup'])
        else: print(report.get('error',report.get('restore_error','No valid hardware result')))
        return 0 if report['valid_for_hardware_report'] else 1
    except Exception as exc: print('性能测试失败：'+str(exc)); return 1
    finally: board.close()
