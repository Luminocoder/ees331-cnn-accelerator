"""Experimental PC-side serial framing. Not a frozen board interface."""

from dataclasses import dataclass
import struct
import time
import zlib


MAGIC = b"MNST"
VERSION = 1
HEADER = struct.Struct("<4sBBIH")
CRC = struct.Struct("<I")
RESULT = struct.Struct("<B10i")
HELLO, IMAGE, HELLO_ACK, SCORES, ERROR = 1, 2, 0x81, 0x82, 0xFF
IDENTITY = b"MNIST-U8-28x28"
MAX_PAYLOAD = 1024


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Packet:
    kind: int
    sequence: int
    payload: bytes


def encode_packet(kind, sequence, payload):
    payload = bytes(payload)
    if not 0 <= kind <= 255 or not 0 <= sequence <= 0xFFFFFFFF:
        raise ProtocolError("Invalid packet kind or sequence")
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("Payload exceeds 1024 bytes")
    body = HEADER.pack(MAGIC, VERSION, kind, sequence, len(payload)) + payload
    return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def decode_packet(data):
    if len(data) < HEADER.size + CRC.size:
        raise ProtocolError("Truncated packet")
    magic, version, kind, sequence, size = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION:
        raise ProtocolError("Wrong packet magic or version")
    if size > MAX_PAYLOAD or len(data) != HEADER.size + size + CRC.size:
        raise ProtocolError("Invalid packet length")
    if CRC.unpack_from(data, len(data) - CRC.size)[0] != zlib.crc32(data[:-4]) & 0xFFFFFFFF:
        raise ProtocolError("Packet CRC mismatch")
    return Packet(kind, sequence, bytes(data[HEADER.size:-CRC.size]))


def image_packet(pixels, sequence):
    if len(pixels) != 28 * 28:
        raise ProtocolError("Image must contain exactly 784 unquantized uint8 pixels")
    return encode_packet(IMAGE, sequence, pixels)


def _read_exact(stream, size, deadline):
    data = bytearray()
    while len(data) < size:
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for board response")
        chunk = stream.read(size - len(data))
        if chunk:
            data.extend(chunk)
        else:
            time.sleep(0.001)
    return bytes(data)


def _receive(stream, deadline):
    # Permit bounded boot/console text before a binary packet, not unlimited scanning.
    window = bytearray()
    for _ in range(4096):
        window.extend(_read_exact(stream, 1, deadline))
        window = window[-len(MAGIC):]
        if window == MAGIC:
            header = MAGIC + _read_exact(stream, HEADER.size - len(MAGIC), deadline)
            size = HEADER.unpack(header)[-1]
            if size > MAX_PAYLOAD:
                raise ProtocolError("Board response exceeds payload limit")
            return decode_packet(header + _read_exact(stream, size + CRC.size, deadline))
    raise ProtocolError("No packet magic within 4096 received bytes")


def _write_all(stream, data, deadline):
    offset = 0
    while offset < len(data):
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out writing to board")
        count = stream.write(data[offset:])
        if count is None or count < 0 or count > len(data) - offset:
            raise ProtocolError("Serial write returned an invalid byte count")
        if count:
            offset += count
        else:
            time.sleep(0.001)


def _exchange(stream, request, expected_kind, sequence, timeout):
    deadline = time.monotonic() + timeout
    _write_all(stream, request, deadline)
    reply = _receive(stream, deadline)
    if reply.sequence != sequence:
        raise ProtocolError("Stale or mismatched response sequence")
    if reply.kind == ERROR:
        raise ProtocolError("Board error: " + reply.payload.decode("utf-8", errors="replace"))
    if reply.kind != expected_kind:
        raise ProtocolError("Unexpected response type")
    return reply.payload


def send_image(stream, pixels, sequence, timeout=3.0):
    if not 0 < timeout <= 60:
        raise ValueError("Timeout must be in (0, 60] seconds")
    request = image_packet(pixels, sequence)
    identity = _exchange(stream, encode_packet(HELLO, sequence, IDENTITY),
                         HELLO_ACK, sequence, timeout)
    if identity != IDENTITY:
        raise ProtocolError("Receiver did not acknowledge the draft uint8 protocol")
    response = _exchange(stream, request, SCORES, sequence, timeout)
    if len(response) != RESULT.size:
        raise ProtocolError("Result must contain a digit and ten signed int32 logits")
    digit, *scores = RESULT.unpack(response)
    if digit > 9 or digit != max(range(10), key=lambda i: scores[i]):
        raise ProtocolError("Result digit does not match the logits argmax")
    return {"digit": digit, "logits": scores, "sequence": sequence,
            "source": "board_response_not_independently_verified"}
