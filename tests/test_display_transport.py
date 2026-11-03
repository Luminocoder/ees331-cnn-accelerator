"""Exercise real firmware C via a native UART/MMIO harness, plus ACK timing."""
import ctypes
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'source'),str(ROOT/'tests')]
import mnist_serial as wire
from display_transport import AcknowledgedPagePlayer
from test_batch import Scheduler
from ees331_studio import Board


class FirmwareStream:
    def __init__(self, page_id=0x50414731, corrupt=False):
        self.dll=ctypes.CDLL(str(ROOT/'hardware/build/firmware_emulator.dll'))
        self.dll.reset_emulator.argtypes=[ctypes.c_uint32,ctypes.c_int]
        self.dll.exchange_packet.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]
        self.dll.exchange_packet.restype=ctypes.c_uint32
        self.dll.inspect.argtypes=[ctypes.c_uint32]
        self.dll.inspect.restype=ctypes.c_uint32
        self.dll.reset_emulator(page_id,corrupt)
        self.pending=bytearray()
        self.requests=[]
    def write(self, data):
        self.requests.append(bytes(data))
        response=ctypes.create_string_buffer(1200)
        size=self.dll.exchange_packet(bytes(data),len(data),response)
        self.pending.extend(response.raw[:size])
        return len(data)
    def read(self, count):
        data=bytes(self.pending[:count]); del self.pending[:count]
        return data
    def inspect(self,index): return self.dll.inspect(index)
    def close(self): pass


class FirmwareTests(unittest.TestCase):
    def test_real_c_capability_and_ascii_to_register_readback(self):
        s=FirmwareStream()
        self.assertEqual(wire.get_display_caps(s,1),wire.PAGE_CAPS)
        for page,value in [('01 02 03',0x01f02f03),('00 09   ',0x00f09fff),('        ',0xffffffff),('45 67 89',0x45f67f89)]:
            self.assertEqual(wire.set_display_page(s,page,2),page)
            self.assertEqual(s.inspect(0),value)
            self.assertEqual(s.inspect(1),6)

    def test_invalid_pages_crc_and_value_rejected_without_writing(self):
        s=FirmwareStream()
        wire.set_display_page(s,'01 02 03',1)
        for payload in [b'01 02 0',b'01 02 0x',b'012345678',bytes([255])*8]:
            with self.assertRaises(wire.ProtocolError):
                wire._exchange(s,wire.encode_packet(wire.DISPLAY_PAGE,2,payload),wire.DISPLAY_PAGE_ACK,2,1)
            self.assertEqual(s.inspect(0),0x01f02f03)
        bad=bytearray(wire.encode_packet(wire.DISPLAY_PAGE,3,b'45 67 89')); bad[-1]^=1
        s.write(bad)
        self.assertEqual(wire.decode_packet(bytes(s.pending)).kind,wire.ERROR)
        self.assertEqual(s.inspect(0),0x01f02f03)

    def test_mixed_firmware_and_pl_and_corrupt_readback_are_errors(self):
        for s in [FirmwareStream(0xdeadbeef),FirmwareStream(corrupt=True)]:
            with self.assertRaises(wire.ProtocolError): wire.set_display_page(s,'01 02 03',2)

    def test_inference_scores_quantization_legacy_display_and_deferred_mode(self):
        s=FirmwareStream()
        wire.set_display_mode(s,True,1)
        wire.set_display_page(s,'01 02 03',2)
        pixels=bytes([0]*783+[255])
        result=wire.send_image(s,pixels,3)
        self.assertEqual(result['logits'],[i*28 for i in range(10)])
        self.assertEqual(result['digit'],9)
        self.assertEqual([s.inspect(i) for i in [0,1,2,3,4]],[0x01f02f03,6,784,0,127])
        wire.set_display_mode(s,False,4)
        wire.send_image(s,pixels,5)
        self.assertEqual(s.inspect(0),9)
        self.assertEqual(wire.set_display(s,49,6),49)
        self.assertEqual(s.inspect(0),49)
        with self.assertRaises(wire.ProtocolError):
            wire._exchange(s,wire.encode_packet(wire.DISPLAY,7,wire.DISP.pack(100)),wire.DISPLAY_ACK,7,1)

    def test_board_negotiates_and_guards_changed_connection(self):
        b=Board(); b.mode='board'; b.stream=FirmwareStream()
        self.assertEqual(b.set_display_page('01 02 03',b.connection_id),'01 02 03')
        b.set_display(49)
        self.assertEqual(b.stream.inspect(5),1)
        with self.assertRaises(RuntimeError): b.set_display_page('01 02 03',b.connection_id+1)
        b.stream=FirmwareStream(0xdeadbeef); b.page_capable=None
        self.assertFalse(b.set_capture_display(True))
        with self.assertRaisesRegex(RuntimeError,'不支持八位分页'):
            b.set_display_page('01 02 03',b.connection_id)


class AckTimingTests(unittest.TestCase):
    def prepare(self):
        self.scheduler=Scheduler(); self.player=AcknowledgedPagePlayer(self.scheduler)
        self.pending=[]; self.shown=[]; self.errors=[]; self.done=[]; self.sent=[]
        self.player.play(['01 02 03','04      '],lambda p:self.sent.append(p) or p,
            lambda work,done:self.pending.append((work,done)),lambda *a:None,
            lambda *a:self.shown.append(a),lambda:self.done.append(True),lambda *a:self.errors.append(a))
    def accept(self):
        work,done=self.pending.pop(0); done(work(),None)
    def test_five_seconds_counted_after_ack_and_last_page_is_retained(self):
        self.prepare(); self.scheduler.advance(9000)
        self.assertFalse(self.shown); self.assertFalse(self.scheduler.jobs)
        self.accept(); self.scheduler.advance(4999); self.assertFalse(self.pending)
        self.scheduler.advance(1); self.assertEqual(len(self.pending),1)
        self.accept(); self.scheduler.advance(5000)
        self.assertEqual(self.sent,['01 02 03','04      ']); self.assertTrue(self.done)
        self.assertFalse(self.player.playing)
    def test_stop_drops_pending_send_and_old_reply(self):
        self.prepare(); self.player.stop(); self.accept(); self.scheduler.advance(10000)
        self.assertFalse(self.sent); self.assertFalse(self.shown); self.assertFalse(self.done)
    def test_bad_ack_and_timeout_stop_instead_of_reporting_success(self):
        for ack,error in [('wrong',None),(None,TimeoutError('no ACK'))]:
            self.prepare(); work,done=self.pending.pop(); done(ack,error)
            self.assertTrue(self.errors); self.assertFalse(self.shown)
            self.assertFalse(self.player.playing); self.assertFalse(self.scheduler.jobs)


if __name__=='__main__': unittest.main()
