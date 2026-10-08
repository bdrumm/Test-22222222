from mta_delay_insights.sources import goodservice as gs


def test_normalize_and_flags_from_routes_payload():
    payload = {"routes": {
        "3": {"id": "3", "status": "Not Good", "direction_statuses": {"north": "Not Good", "south": "Not Good"},
              "delay_summaries": {"north": None, "south": None},
              "service_irregularity_summaries": {"north": "Harlem-bound trains are having longer wait times between New Lots Av and 116 St (up to 24 mins, normally every 10 mins).", "south": None},
              "service_change_summaries": {"both": [], "north": [], "south": []}, "slow_sections": {"north": [], "south": []},
              "long_headway_sections": {"north": [{"from": "257", "to": "224"}], "south": []}, "max_delay": 0,
              "actual_routings": {"north": [["257", "120"]], "south": [["120", "257"]]}, "scheduled_routings": {"north": [["257", "120"]], "south": [["120", "257"]]}},
        "A": {"id": "A", "status": "Service Change", "actual_routings": {"north": [["A02", "A10"]]}, "scheduled_routings": {"north": [["A02", "A09", "A10"]]},
              "service_change_summaries": {"north": ["A trains are rerouted"]}},
        "L": {"id": "L", "status": "Good Service"},
    }}
    df = gs.normalize(payload, ts=100.0)
    assert list(df.columns) == gs.COLUMNS and len(df) == 3
    three = df[df["route"] == "3"].iloc[0]
    assert three["n_long_headway_sections"] == 1 and three["irregularity_north"].startswith("Harlem") and not three["rerouted"]
    a = df[df["route"] == "A"].iloc[0]
    assert a["rerouted"] and a["n_service_changes"] == 1
    flags = gs.route_flags(df)
    assert flags["3"]["bad"] and flags["3"]["irregular"] and not flags["3"]["slow"]
    assert flags["A"]["bad"] and flags["L"] == {"bad": False, "slow": False, "irregular": False, "status": "Good Service", "text": ""}
    assert gs.normalize({}).empty and gs.route_flags(None) == {}
