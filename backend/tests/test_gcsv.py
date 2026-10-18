import pytest

from app.errors import CraftsError
from app.gcsv import parse_gcsv_text


HEADER = """GYROFLOW IMU LOG
version,1.3
id,custom_logger
orientation,YxZ
note,unit-test
lensprofile,potatocam/prime
lens_info,wide
frame_readout_time,15.23
frame_readout_direction,0
tscale,0.001
gscale,0.001
ascale,0.01
t,gx,gy,gz,ax,ay,az
"""


def test_scales_units_and_optional_lens_fields():
    text = HEADER + "0,10,-20,30,100,0,-50\n1,10,-20,30,100,0,-50\n2,0,0,0,0,0,0\n"
    log = parse_gcsv_text(text)
    assert log.sample_rate_hz == pytest.approx(1000)
    assert log.samples[0].t_seconds == pytest.approx(0)
    assert log.samples[1].t_seconds == pytest.approx(0.001)
    assert log.samples[0].gyro_rad_s == pytest.approx((0.01, -0.02, 0.03))
    assert log.samples[0].accel_g == pytest.approx((1.0, 0.0, -0.5))
    assert log.orientation == "YxZ"
    assert log.lensprofile == "potatocam/prime"
    assert log.lens_info == "wide"
    assert log.frame_readout_time_ms == pytest.approx(15.23)
    assert log.frame_readout_direction == 0
    assert log.gaps == []


def test_negative_raw_values_stay_signed():
    text = HEADER + "0,-100,0,5,0,0,0\n5,-50,-1,-2,0,0,0\n"
    sample = parse_gcsv_text(text).samples[0]
    assert sample.gyro_raw[0] == -100
    assert sample.gyro_rad_s[0] == pytest.approx(-0.1)


def test_rejects_wrong_column_order():
    text = """GYROFLOW IMU LOG
version,1.3
id,custom_logger
orientation,XYZ
tscale,0.001
gscale,0.001
t,gy,gx,gz
0,1,2,3
1,1,2,3
"""
    with pytest.raises(CraftsError, match="Column order"):
        parse_gcsv_text(text)


def test_rejects_missing_scale_and_bad_orientation():
    with pytest.raises(CraftsError, match="gscale"):
        parse_gcsv_text(
            """CAMERA IMU LOG
version,1.1
id,logger
orientation,XYZ
tscale,0.001
t,gx,gy,gz
0,1,2,3
1,1,2,3
"""
        )
    with pytest.raises(CraftsError, match="orientation"):
        parse_gcsv_text(
            """GYROFLOW IMU LOG
version,1.3
id,logger
orientation,XxY
tscale,0.001
gscale,0.001
t,gx,gy,gz
0,1,2,3
1,1,2,3
"""
        )


def test_missing_ascale_is_a_controlled_error_not_keyerror():
    text = """GYROFLOW IMU LOG
version,1.3
id,custom_logger
orientation,XYZ
tscale,0.001
gscale,0.001
t,gx,gy,gz,ax,ay,az
0,1,2,3,4,5,6
1,1,2,3,4,5,6
"""
    with pytest.raises(CraftsError, match="ascale") as caught:
        parse_gcsv_text(text)
    assert caught.value.code == "bad_gcsv"


def test_nan_and_inf_samples_are_rejected():
    for bad in ("nan", "inf", "-inf"):
        text = HEADER + f"0,1,2,3,0,0,0\n1,{bad},2,3,0,0,0\n"
        with pytest.raises(CraftsError, match="non-finite") as caught:
            parse_gcsv_text(text)
        assert caught.value.code == "bad_gcsv"
    text = """GYROFLOW IMU LOG
version,1.3
id,custom_logger
orientation,XYZ
tscale,nan
gscale,0.001
t,gx,gy,gz
0,1,2,3
1,1,2,3
"""
    with pytest.raises(CraftsError, match="tscale") as caught:
        parse_gcsv_text(text)
    assert caught.value.code == "bad_gcsv"


def test_detects_timestamp_gaps_and_rejects_decreasing_time():
    text = HEADER + "0,0,0,0,0,0,0\n1,0,0,0,0,0,0\n2,0,0,0,0,0,0\n50,0,0,0,0,0,0\n"
    log = parse_gcsv_text(text)
    assert len(log.gaps) == 1
    assert log.gaps[0].index == 3
    assert log.gaps[0].dt_seconds == pytest.approx(0.048)
    with pytest.raises(CraftsError, match="increasing"):
        parse_gcsv_text(HEADER + "0,0,0,0,0,0,0\n0,1,1,1,0,0,0\n")
