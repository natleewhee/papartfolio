from config import daily_report_day_of_week


def test_daily_report_day_of_week_before_sg_open_is_tue_sat():
    assert daily_report_day_of_week("05:00") == "tue-sat"
    assert daily_report_day_of_week("00:00") == "tue-sat"
    assert daily_report_day_of_week("08:59") == "tue-sat"


def test_daily_report_day_of_week_at_or_after_sg_open_is_mon_fri():
    assert daily_report_day_of_week("09:00") == "mon-fri"  # exactly SG open
    assert daily_report_day_of_week("20:30") == "mon-fri"  # the default
    assert daily_report_day_of_week("23:59") == "mon-fri"
