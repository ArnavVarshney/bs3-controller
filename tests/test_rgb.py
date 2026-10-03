from bs3 import rgb as R


def test_effect_names_cover_0_to_5():
    assert sorted(R.EFFECT_NAMES) == [0, 1, 2, 3, 4, 5]


def test_make_header_layout():
    h = R.make_header(0x00, 0x0A, 70, (104, 211, 145))
    assert len(h) == R.HEADER_LEN == 10
    assert h[5] == 70 and tuple(h[6:9]) == (104, 211, 145)
    try:
        R.make_header(brightness=101)
    except ValueError:
        pass
    else:
        raise AssertionError("brightness 101 must raise")


def test_static_color_frames_shape():
    header, frames = R.static_color_frames((104, 211, 145), 70)
    assert len(header) == 10
    assert len(frames) == R.N_DATA_FRAMES
    assert all(len(f) == 10 for f in frames)
    # brightness scaling lands in the lit triplets
    lit = [f for f in frames if any(f)]
    assert lit, "some frames must carry LED data"


def test_upload_plan_indices():
    header, frames = R.static_color_frames((255, 0, 0), 100)
    plan = R.upload_plan(header, frames)
    assert plan[0][0] == 0x47 and plan[0][1][0] == 0x00  # header frame
    idx = [p[0] for _, p in plan[1:]]
    assert idx == list(range(1, len(frames) + 1))  # 0x01..0x12, tail is 6B
    assert len(plan[-1][1]) == 1 + 6
    try:
        R.upload_plan(b"short", frames)
    except ValueError:
        pass
    else:
        raise AssertionError("bad header must raise")
