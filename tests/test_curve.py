from bs3 import curve as C


def test_target_below_first_is_stop():
    cv = C.Curve()
    assert cv.target_for(20.0) == 0
    assert cv.target_for(34.9) == 0


def test_target_interpolates():
    cv = C.Curve(points=[(35.0, 1000), (45.0, 1400)])
    assert cv.target_for(35.0) == 1000
    assert cv.target_for(40.0) == 1200
    assert cv.target_for(45.0) == 1400
    assert cv.target_for(99.0) == 1400  # at/above last: hold top


def test_update_deadband_skips_rewrites():
    cv = C.Curve()
    want, changed = cv.update(60.0)
    assert changed and want > 0
    want2, changed2 = cv.update(60.0)
    assert not changed2 and want2 == want  # within 100 RPM: skip the write


def test_update_stop_transition_always_sent():
    cv = C.Curve()
    cv.update(60.0)
    # smoothing (slow down) takes several cold readings to reach stop...
    saw_stop = False
    for _ in range(60):
        want, changed = cv.update(20.0)
        if want == 0:
            assert changed  # 0-transition always sent, whatever the deadband
            saw_stop = True
            break
    assert saw_stop
    _, changed = cv.update(20.0)
    assert not changed  # settled at stop: nothing to send


def test_update_supply_ceiling():
    cv = C.Curve()
    want, _ = cv.update(85.0, supply=1)  # laptop USB: 2700 cap
    assert want == 2700


def test_update_model_ceiling():
    cv = C.Curve()
    want, _ = cv.update(85.0, supply=3, model="BS3")  # motor saturates ~3400
    assert want == 3400
    want, _ = cv.update(85.0, supply=3, model="BS3 Pro")  # unverified: rating stands
    assert want == 4000


def test_update_panic_bypasses_deadband():
    cv = C.Curve()
    cv.update(60.0)
    want, changed = cv.update(95.0, supply=3)
    assert changed and want == 4000
    want, _ = cv.update(95.0, supply=3, model="BS3")
    assert want == 3400
