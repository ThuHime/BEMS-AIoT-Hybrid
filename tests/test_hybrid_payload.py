"""Unit tests for the one-real-PZEM / nineteen-virtual-room data contract."""
from backend.server_ai_bems import parse_room


def test_room_01_is_tagged_as_physical_pzem():
    row = {
        "id": 1,
        "source": "pzem004t_v3",
        "simulated": False,
        "valid": True,
        "voltage_v": 230.1,
        "current_a": 0.42,
        "power_w": 85.0,
        "energy_kwh": 12.345,
        "power_factor": 0.88,
        "frequency_hz": 50.0,
    }
    value = parse_room(row)
    assert value.room_id == 1
    assert value.simulated is False
    assert value.valid is True
    assert value.power_w == 85.0


def test_room_02_to_20_are_synthetic():
    row = {
        "id": 20,
        "source": "synthetic_generator",
        "simulated": True,
        "valid": True,
        "voltage_v": 230.0,
        "current_a": 0.8,
        "power_w": 165.0,
        "energy_kwh": 2.5,
        "power_factor": 0.9,
        "frequency_hz": 50.0,
    }
    value = parse_room(row)
    assert value.room_id == 20
    assert value.simulated is True
    assert value.valid is True


def test_room_01_must_not_be_marked_simulated():
    import pytest
    from backend.server_ai_bems import BemsService

    # Test the contract validation without creating the service (and its clients).
    service = object.__new__(BemsService)
    room_rows = []
    for room_id in range(1, 21):
        room_rows.append({
            "id": room_id,
            "source": "pzem004t_v3" if room_id == 1 else "synthetic_generator",
            "simulated": room_id != 1,
            "valid": True,
            "voltage_v": 230.0,
            "current_a": 0.1,
            "power_w": 10.0,
            "energy_kwh": 0.1,
            "power_factor": 0.9,
            "frequency_hz": 50.0,
        })

    # Trigger the source-contract check while preventing later Influx/AI calls.
    room_rows[0]["simulated"] = True
    room_rows[0]["source"] = "synthetic_generator"
    with pytest.raises(ValueError, match="Room 01 must have simulated=false"):
        service.process_telemetry({"rooms": room_rows})
