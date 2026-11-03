"""Actual CPU C correctness; emulated UART flow must never yield hardware data."""
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'source'),str(ROOT/'tests')]
import arm_benchmark as bench
import mnist_serial as wire
import offline_ref
from test_display_transport import FirmwareStream

class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.stream=FirmwareStream()
        self.dll=self.stream.dll
        self.dll.test_cpu.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        self.metadata=json.loads((ROOT/'resources/benchmark/metadata.json').read_text())
    def cpu(self,pixels):
        values=(ctypes.c_int32*10)(); self.dll.test_cpu(pixels.tobytes(),values); return list(values)
    def test_actual_c_all_twenty_and_all_two_hundred_logits(self):
        for i in range(20):
            raw=bytes(int(t,16) for t in (ROOT/('resources/samples/_frozen/img_%02d.txt'%i)).read_text().split())
            self.assertEqual(self.cpu(np.frombuffer(raw,np.uint8)),self.metadata['expected'][i])
    def test_actual_c_extremes_random_and_border_strokes_against_python(self):
        rng=np.random.default_rng(331)
        border=np.zeros((28,28),np.uint8); border[[0,-1],:]=255; border[:,[0,-1]]=255
        for pixels in [np.zeros((28,28),np.uint8),np.full((28,28),255,np.uint8),border,
                       rng.integers(0,256,(28,28),np.uint8),rng.integers(0,256,(28,28),np.uint8)]:
            self.assertEqual(self.cpu(pixels),offline_ref.infer(pixels,ROOT/'resources')[1])
    def run_flow(self,**kw):
        report,path=bench.run_on_stream(self.stream,ROOT/'resources',Path(self.temp),
                                        evidence_kind='firmware_host_emulator',**kw)
        self.assertEqual(bench.decode_info(wire._exchange(self.stream,wire.encode_packet(7,100,b''),0x87,100,1))['active'],0)
        self.assertIsNone(report['speedup']); self.assertFalse(report['valid_for_hardware_report'])
        self.assertTrue((path/'rows.csv').is_file()); self.assertTrue((path/'wire.jsonl').is_file())
        return report,path
    def test_full_real_c_protocol_flow_130_pairs_no_claim_of_hardware_speedup(self):
        with tempfile.TemporaryDirectory() as self.temp:
            report,path=self.run_flow()
            self.assertEqual(report['status'],'EMULATED_FLOW_PASS_NOT_A_MEASUREMENT')
            self.assertEqual(len(report['rows']),130)
            formal=[r for r in report['rows'] if r['phase']=='formal']
            self.assertEqual(len(formal),100)
            self.assertEqual({i:sum(r['index']==i for r in formal) for i in range(20)},{i:5 for i in range(20)})
            self.assertEqual({r['order'] for r in formal},{0,1})
            self.assertFalse(report['camera_opened']); self.assertFalse(report['hardware_accessed'])
            self.assertEqual(report['restored']['sctlr'],report['before']['sctlr'])
    def test_corrupt_pl_stopped_timer_and_accelerator_error_abort_and_restore(self):
        for fault in (1,2,3):
            self.stream=FirmwareStream(); self.dll=self.stream.dll; self.dll.set_bench_fault(fault)
            with tempfile.TemporaryDirectory() as self.temp:
                report,path=self.run_flow()
                self.assertEqual(report['status'],'FAILED'); self.assertEqual(report['rows'],[])
                self.assertTrue((path/'last_reply.hex').is_file())
    def test_cancel_keeps_partial_records_and_restores(self):
        calls=[]
        def cancel(): calls.append(1); return len(calls)>35
        with tempfile.TemporaryDirectory() as self.temp:
            report,path=self.run_flow(cancel=cancel)
            self.assertEqual(report['status'],'CANCELLED'); self.assertEqual(len(report['rows']),35)
    def test_invalid_commands_and_normal_inference_during_benchmark_rejected(self):
        def exchange(k,p): return wire._exchange(self.stream,wire.encode_packet(k,2,p),k|128,2,1)
        for kind,payload in [(9,b'\0\0'),(7,b'x'),(8,b'\2')]:
            with self.assertRaises(wire.ProtocolError): exchange(kind,payload)
        exchange(8,b'\1')
        for kind,payload in [(9,b'\24\0'),(9,b'\0\2'),(2,bytes(784)),(8,b'\1')]:
            with self.assertRaises(wire.ProtocolError): exchange(kind,payload)
        self.assertEqual(bench.decode_info(exchange(8,b'\0'))['active'],0)
    def test_wrong_identity_input_crc_and_timebase_are_rejected(self):
        from io import StringIO
        trace=bench.Trace(self.stream,StringIO()); info=trace.info()
        bench.verify_identity(info,self.metadata)
        info['hashes']['model']='00'*32
        with self.assertRaises(ValueError): bench.verify_identity(info,self.metadata)
        active=bench.decode_info(trace.exchange(8,b'\1'))
        raw=bytearray(trace.exchange(9,b'\0\0')); raw[24]^=1
        with self.assertRaisesRegex(ValueError,'CRC'): bench.validate_row(raw,0,0,self.metadata,active)
        with self.assertRaisesRegex(ValueError,'交叉检查'):
            bench.calibrate(trace,active,bench.clock_rates(active))
        trace.exchange(8,b'\0')
    def test_clock_decode_and_sum_ratio(self):
        from io import StringIO
        info=bench.Trace(self.stream,StringIO()).info(); rates=bench.clock_rates(info)
        self.assertAlmostEqual(rates['arm_hz'],666666660)
        self.assertAlmostEqual(rates['pl_hz'],49999999.5)
        self.assertAlmostEqual(rates['timer_hz'],333333330)
        s=bench.summary([{'cpu_ticks':100,'pl_ticks':10},{'cpu_ticks':200,'pl_ticks':100}],1000)
        self.assertAlmostEqual(s['speedup'],300/110)
        info['arm_clock']=0
        with self.assertRaises(ValueError): bench.clock_rates(info)
    def test_cli_requires_explicit_port_and_rejects_conflicting_actions_before_devices(self):
        import ees331_studio as studio
        from contextlib import redirect_stderr
        from io import StringIO
        for args in (['--benchmark'],['--benchmark','--port','COM5','--offline'],
                     ['--benchmark','--port','COM5','--initialize'],['--benchmark','--port','COM5','--selftest']):
            with redirect_stderr(StringIO()),patch.object(studio.serial_link,'open_port',side_effect=AssertionError('no devices')):
                with self.assertRaises(SystemExit) as exc: studio.main(args)
                self.assertEqual(exc.exception.code,2)

if __name__=='__main__': unittest.main()
