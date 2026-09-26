"""Codec tests with byte vectors taken from captures and prior implementations."""
import pytest

from pycrestron_cip import protocol as p
from pycrestron_cip.protocol import JoinType, JoinUpdate


def h(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


def decode(data: bytes) -> list[p.Message]:
    frames = p.FrameDecoder().feed(data)
    return [m for t, pl in frames for m in p.decode_frame(t, pl)]


# --- building -------------------------------------------------------------------------

def test_build_connect():
    assert p.build_connect(0xA0) == h("01 000b 0000000000 a0 40 ffff f1 01")
    with pytest.raises(ValueError):
        p.build_connect(0x02)


def test_build_commands_and_heartbeat():
    assert p.build_command(p.Command.UPDATE_REQUEST) == h("05 0005 0000 02 03 00")
    assert p.build_command(p.Command.END_OF_QUERY_ACK) == h("05 0005 0000 02 03 1d")
    assert p.build_heartbeat() == h("0d 0002 0000")
    assert p.build_heartbeat(response=True) == h("0e 0002 0000")


def test_build_digital():
    assert p.build_digital(1, True) == h("05 0006 0000 03 00 00 00")
    assert p.build_digital(1, False) == h("05 0006 0000 03 00 00 80")
    assert p.build_digital(101, True, repeat=True) == h("05 0006 0000 03 27 64 00")
    assert p.build_digital(300, False) == h("05 0006 0000 03 00 2b 81")  # 299 = 0x012b
    with pytest.raises(ValueError):
        p.build_digital(0, True)


def test_build_analog():
    assert p.build_analog(361, 65535) == h("05 0008 0000 05 14 0168 ffff")
    with pytest.raises(ValueError):
        p.build_analog(1, 70000)


def test_build_serial_ascii_matches_cipclient():
    # python-cipclient: 12 00 <8+n> 00 00 00 <4+n> 34 <join:2> 03 <text>
    assert p.build_serial(10, "Hi") == [h("12 000a 0000 0006 34 0009 03 4869")]


def test_build_serial_unicode_and_chunks():
    [frame] = p.build_serial(1, "Кухня")
    assert frame[7:11] == h("34 0000 07")  # UTF-16LE, start+end
    frames = p.build_serial(1, "x" * 500)
    assert len(frames) == 3
    assert [f[10] for f in frames] == [p.SERIAL_START, 0, p.SERIAL_END]
    assert decode(b"".join(frames)) and _assemble(decode(b"".join(frames))) == "x" * 500


def _assemble(messages):
    asm = p.SerialAssembler()
    out = [asm.add(m) for m in messages if isinstance(m, p.SerialChunk)]
    return next(u.value for u in out if u is not None)


# --- decoding -------------------------------------------------------------------------

def test_decode_digital_analog():
    assert decode(h("05 0006 0000 03 00 64 00")) == [JoinUpdate(JoinType.DIGITAL, 101, True)]
    assert decode(h("05 0006 0000 03 00 64 80")) == [JoinUpdate(JoinType.DIGITAL, 101, False)]
    assert decode(h("05 0006 0000 03 27 2b 01")) == [JoinUpdate(JoinType.DIGITAL, 300, True)]
    assert decode(h("05 0008 0000 05 14 0168 9999")) == [JoinUpdate(JoinType.ANALOG, 361, 0x9999)]
    assert decode(h("05 0007 0000 04 01 05 0100")) == [JoinUpdate(JoinType.ANALOG, 6, 256)]  # legacy


def test_decode_several_subpackets_in_one_frame():
    msgs = decode(h("05 000a 0000 03 00 00 00 03 00 01 80"))
    assert msgs == [JoinUpdate(JoinType.DIGITAL, 1, True), JoinUpdate(JoinType.DIGITAL, 2, False)]


def test_decode_packed_joins_from_real_dump():
    # Initial dump captured from a 3-Series: several joins share one sub-packet.
    digitals = decode(h("05 0012 0000 0f 00 3100 3b00 6b00 5d01 f301 b70b bf0b"))
    assert [(u.join, u.value) for u in digitals] == [
        (50, True), (60, True), (108, True), (350, True), (500, True), (3000, True), (3008, True)]
    assert decode(h("05 000c 0000 09 14 000b 0001 000e 0001")) == [
        JoinUpdate(JoinType.ANALOG, 12, 1), JoinUpdate(JoinType.ANALOG, 15, 1)]
    nested = decode(h("05 0012 0000 0f 38 000000 01 09 00 1100 da07 db07 dc87"))
    assert nested == [JoinUpdate(JoinType.DIGITAL, 18, True, 1), JoinUpdate(JoinType.DIGITAL, 2011, True, 1),
                      JoinUpdate(JoinType.DIGITAL, 2012, True, 1), JoinUpdate(JoinType.DIGITAL, 2013, False, 1)]


def test_decode_command_with_data():
    # seen right after the initial dump on a 3-Series; kept raw, meaning unknown
    assert decode(h("05 0009 0000 06 03 19 434c434d")) == [p.CommandMessage(0x19, h("434c434d"))]


def test_decode_serial_variants():
    assert _assemble(decode(h("12 000a 0000 0006 34 0009 03 4869"))) == "Hi"
    assert _assemble(decode(p.build_serial(3, "Кухня")[0])) == "Кухня"
    assert _assemble(decode(h("05 0009 0000 06 15 0001 03 6f6b"))) == "ok"  # short form
    assert _assemble(decode(h("12 0009 0000 0005 34 0001 00 ff"))) == "ÿ"  # not UTF-8: latin-1


def test_decode_smart_object():
    msgs = decode(h("05 000c 0000 09 38 000000 05 03 00 04 00"))
    assert msgs == [JoinUpdate(JoinType.DIGITAL, 5, True, smart_object=5)]


def test_decode_control_frames():
    assert decode(h("0f 0001 02")) == [p.ProgramStatusMessage(2)]
    assert decode(h("02 0004 0000001f")) == [p.ConnectResponse(True, h("0000001f"))]
    assert decode(h("02 0004 0000200f"))[0].accepted  # variant seen in the field
    assert not decode(h("02 0003 ffff02"))[0].accepted
    assert decode(h("05 0005 0000 02 03 1c")) == [p.CommandMessage(p.Command.END_OF_QUERY)]
    assert decode(h("0d 0002 0000")) == [p.Heartbeat(False)]
    assert decode(h("03 0000")) == [p.Disconnect()]
    assert isinstance(decode(h("05 000b 0000 08 08 0e181155032720"))[0], p.DateTimeMessage)


def test_decode_errors():
    with pytest.raises(p.ProtocolError):
        p.decode_frame(0x05, h("0000 09 00 00"))  # sub-packet longer than the frame
    assert p.decode_frame(0x05, h("0000 03 99 00 00")) == []  # unknown sub-type is skipped
    assert p.decode_frame(0x77, b"x") == [p.Unknown(0x77, b"x")]


def test_frame_decoder_split_and_coalesced():
    data = p.build_digital(1, True) + p.build_analog(2, 5) + p.build_heartbeat()
    dec = p.FrameDecoder()
    frames = []
    for i in range(len(data)):  # one byte at a time
        frames += dec.feed(data[i:i + 1])
    assert [t for t, _ in frames] == [0x05, 0x05, 0x0D] and dec.pending == 0
    assert len(p.FrameDecoder().feed(data)) == 3


@pytest.mark.parametrize("join", [1, 2, 255, 256, 257, 1000, 0x8000])
def test_digital_roundtrip(join):
    for value in (True, False):
        assert decode(p.build_digital(join, value)) == [JoinUpdate(JoinType.DIGITAL, join, value)]
