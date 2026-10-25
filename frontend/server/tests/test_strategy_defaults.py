from server.strategy_defaults import default_snapshot, default_strategy


def test_current_best_is_preferred_and_historical_fallback_is_explicit(tmp_path):
    folder = tmp_path / "amat/configs/strategy"
    folder.mkdir(parents=True)
    (folder / "amat_v6.toml").touch()
    assert default_strategy(tmp_path) == "amat_v6.toml"
    (folder / "amat_group_selection_daily.toml").touch()
    assert default_strategy(tmp_path) == "amat_group_selection_daily.toml"
    (folder / "amat_group_selection_volatility.toml").touch()
    assert default_strategy(tmp_path) == "amat_group_selection_volatility.toml"
    assert (
        default_snapshot(["old", "alpaca-sip-core-2026-10-13-verified", "new"])
        == "alpaca-sip-core-2026-10-13-verified"
    )


def test_guided_controls_and_historical_builder_use_current_best(tmp_path):
    from types import SimpleNamespace
    from server.control_actions import capabilities as controls
    from server.engine_actions import capabilities as engines, command

    folder = tmp_path / "amat/configs/strategy"
    folder.mkdir(parents=True)
    (folder / "amat_group_selection_volatility.toml").touch()
    runtime = tmp_path / "amat/.venv/bin"
    runtime.mkdir(parents=True)
    (runtime / "python").touch()
    snapshot = tmp_path / "amat/data/snapshots/alpaca-sip-core-2026-10-13-verified"
    snapshot.mkdir(parents=True)
    (snapshot / "daily.parquet").touch()
    caps = controls(tmp_path, tmp_path / "state")
    scan = next(c for c in caps if c["id"] == "amat.scan")
    assert scan["inputs"]["config"]["default"] == "amat_group_selection_volatility.toml"
    historical = engines(tmp_path, tmp_path / "state")[0]
    assert historical["inputs"]["version"]["default"] == "daily-volatility"
    args, _, _ = command(
        SimpleNamespace(root=tmp_path, storage=tmp_path / "state"),
        SimpleNamespace(capability="amat.backtest", inputs={"snapshot": snapshot.name}),
    )
    assert args[args.index("--strategy-version") + 1] == "daily-volatility"
