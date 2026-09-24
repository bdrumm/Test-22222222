from mta_delay_insights import cli
from mta_delay_insights.sources.registry import catalog, format_table


def test_sources_catalog(capsys):
    cli.main(["sources"])
    out = capsys.readouterr().out
    assert "rt:ace" in out and "open:trains_delayed" in out and "weather:open_meteo" in out
    keys = {s.key for s in catalog()}
    assert {"static:subway", "alerts:subway_alerts_json", "open:hourly_ridership_2025"} <= keys
    assert format_table().count("\n") > 20


def test_static_commands(mini_gtfs_dir, capsys):
    cli.main(["static", "stations", "--gtfs", str(mini_gtfs_dir), "--query", "Union"])
    assert "14 St-Union Sq" in capsys.readouterr().out
    cli.main(["static", "platform", "--gtfs", str(mini_gtfs_dir), "--station", "631", "--direction", "N"])
    out = capsys.readouterr().out
    assert "platform 631N" in out and "upstream" in out
