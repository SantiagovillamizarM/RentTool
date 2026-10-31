"""Horario para recibir mercancía: hora de inicio y de final dentro del reparto (6 a. m. a 8 p. m.)."""

import pytest

from app.hours import HoursError, clean_hours, hours_label, range_parts, segment, time_label


def test_time_labels_in_colombian_style():
    assert time_label(7 * 60) == "7:00 a. m."
    assert time_label(12 * 60) == "12:00 m."
    assert time_label(15 * 60 + 30) == "3:30 p. m."


def test_valid_hours_are_saved_as_a_range():
    assert clean_hours("07:30", "17:00") == "07:30-17:00"
    assert clean_hours("7:10", "9:05") == "07:10-09:05"
    assert clean_hours("06:00", "12:00") == "06:00-12:00"  # se puede terminar justo al almuerzo
    assert clean_hours("13:00", "20:00") == "13:00-20:00"  # o empezar justo después


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("", "12:00", "Escribe desde qué hora"),
        ("7", "12:00", "Escribe desde qué hora"),
        ("00:00", "01:00", "Los camiones reparten de 6 a. m. a 8 p. m. Escribe"),
        ("19:00", "21:00", "Los camiones reparten"),
        ("05:45", "08:00", "Los camiones reparten"),
        ("10:00", "09:00", "después de la inicial"),
        ("12:00", "14:00", "almuerzo"),
        ("10:00", "12:30", "almuerzo"),
        ("12:15", "13:00", "almuerzo"),
    ],
)
def test_invalid_hours_are_rejected(start, end, message):
    with pytest.raises(HoursError, match=message):
        clean_hours(start, end)


def test_labels():
    assert hours_label("06:00-12:00") == "De 6:00 a. m. a 12:00 m."
    assert hours_label("07:00-17:00") == "De 7:00 a. m. a 5:00 p. m., menos el almuerzo (12 m. a 1 p. m.)"
    assert hours_label("7 a 12") == "7 a 12"  # texto viejo escrito a mano


def test_fields_and_bar():
    assert range_parts("07:30-17:00") == ("07:30", "17:00")
    assert range_parts(None) == ("", "")
    assert segment("06:00-20:00") == (0.0, 100.0)
    assert segment("13:00-20:00") == (50.0, 50.0)
    assert segment("basura") is None
