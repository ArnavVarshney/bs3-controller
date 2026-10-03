from bs3 import protocol as P


def test_build_verify_roundtrip():
    f = P.build_frame(0x25)
    assert P.verify_frame(f)
    assert f[2] == 0x25


def test_checksum_known():
    # 02 5A A5 21 04 28 0A 57 -> target 2600 rpm (0x0A28), from PROTOCOL.md capture
    f = P.build_frame(0x21, bytes((0x28, 0x0A)))
    assert f.hex() == "5aa52104280a57"


def test_extract_frame_usb_leading_byte():
    # live USB reads start with a leading byte (seen: 0x03), magic at 1 —
    # same offset rule as BT, despite no report ids in the descriptor
    urep = bytes((0x03,)) + P.build_usb_report(0x25)
    assert P.extract_frame(urep[:32])[2] == 0x25


def test_blocked_commands():
    for cmd in (0xDF, 0x06, 0x03, 0xF1, 0xF2, 0x0A):
        try:
            P.build_frame(cmd)
        except P.UnsafeCommandError:
            continue
        raise AssertionError(f"0x{cmd:02X} should be blocked")


def test_gear_range_enforced():
    try:
        P.build_frame(P.CMD_SELECT_GEAR, b"\x05")
        raise AssertionError("gear 05 must raise")
    except P.UnsafeCommandError:
        pass
    P.build_frame(P.CMD_SELECT_GEAR, b"\x02")  # ok


def test_effect_range_enforced():
    try:
        P.build_frame(P.CMD_SELECT_EFFECT, b"\x06")
        raise AssertionError("effect 06 must raise")
    except P.UnsafeCommandError:
        pass


def test_decode_status_firmware_layout():
    # report: id 01, magic, EF 0D, b5=0x68 (awake/gear1/BLE/supply3), b6=0x03 (realtime),
    # b7=0x05 (gear-led + strip), cur=0x076C, tgt=0x076C, ramp=01, pers=01, seq
    rep = bytes.fromhex("01 5aa5 ef0d 68 03 05 6c07 6c07 01 01 2300 2b".replace(" ", ""))
    rep = rep + bytes(25 - len(rep))
    s = P.decode_status(rep)
    assert s.gear == "quiet"
    assert s.supply == 3 and s.supply_name == "full"
    assert s.realtime is True
    assert s.strip_on is True
    assert s.current_rpm == 0x076C


def test_clamp_rules():
    assert P.clamp_rpm(0) == 0
    assert P.clamp_rpm(300) == 500  # stall band
    assert P.clamp_rpm(4000, supply=1) == 2700
    assert P.clamp_rpm(4000, supply=2) == 3300
    assert P.clamp_rpm(4000, supply=3) == 4000


def test_model_ceiling():
    # measured: base BS3 saturates ~3300-3400 with 4000 commanded at supply 3
    assert P.model_ceiling("BS3") == 3400
    assert P.model_ceiling("BS3 Pro") == P.MAX_RPM  # unverified, keeps rating
    assert P.model_ceiling(None) == P.MAX_RPM
    assert P.model_ceiling("???") == P.MAX_RPM
    assert P.clamp_rpm(4000, supply=3, model="BS3") == 3400
    assert P.clamp_rpm(2000, supply=3, model="BS3") == 2000
    assert P.clamp_rpm(0, supply=3, model="BS3") == 0
    assert P.clamp_rpm(300, supply=3, model="BS3") == 500  # stall band first
    assert P.clamp_rpm(4000) == 4000  # model omitted: old behavior


def test_model_has_strip():
    assert P.MODEL_HAS_STRIP.get("BS3", True) is False  # gear LEDs only
    assert P.MODEL_HAS_STRIP.get("BS3 Pro", True) is True
    assert P.MODEL_HAS_STRIP.get("Demo BS3", True) is True


def test_model_gears():
    # measured: base BS3 ACKs 0x08 gear 04 but 0xEF keeps reporting gear 3
    assert P.model_gears("BS3") == ["quiet", "standard", "strong"]
    assert P.model_gears("BS3 Pro") == P.GEAR_NAMES
    assert P.model_gears("Demo BS3") == P.GEAR_NAMES
    assert P.model_gears(None) == P.GEAR_NAMES
