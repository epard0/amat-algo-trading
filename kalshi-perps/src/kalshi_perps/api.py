"""Public strategy API. Importing it creates no broker, credentials or network client."""

from .config import Config
from .profiles import Strategy, StrategyProfile
from .strategy import SYMBOLS, CryptoStrategy, ProfileStrategy


def describe_strategy(config: Config) -> tuple[StrategyProfile | None, tuple[str, ...], str]:
    """Resolve strategy metadata without invoking or importing the research engine."""
    cfg = config.raw["strategy"]
    if cfg.get("profile"):
        profile = StrategyProfile.load(config.resolve(cfg["profile"]))
        return profile, profile.symbols, profile.identity
    return None, SYMBOLS, CryptoStrategy.policy_identity(cfg["source_sha256"])


def create_strategy(config: Config) -> Strategy:
    """Construct a source-pinned engine adapter independently of trading mode."""
    profile, _, _ = describe_strategy(config)
    cfg = config.raw["strategy"]
    root, python = config.resolve(cfg["amat_root"]), config.resolve(cfg["python"])
    return (
        ProfileStrategy(profile, root, python)
        if profile
        else CryptoStrategy(root, python, cfg["source_sha256"])
    )


class StrategyService:
    """Easy entry point for applications and notebooks.

    service = StrategyService.from_config('configs/paper.toml')
    service.describe()
    service.decide(completed_hourly_candles, as_of_ms)
    """

    def __init__(self, config: Config):
        self.config = config
        self.engine = create_strategy(config)

    @classmethod
    def from_config(cls, path, *, strategy=None):
        config = Config.load(path)
        if strategy:
            config = config.select_strategy(strategy)
        return cls(config)

    @property
    def symbols(self):
        return self.engine.symbols

    def describe(self):
        profile, symbols, identity = describe_strategy(self.config)
        return dict(
            name=profile.name if profile else "btc-eth-reference",
            symbols=list(symbols),
            identity=identity,
            profile=profile.payload() if profile else None,
            decision_contract="crypto-perps-decision/1",
            interval="completed-hourly-input / four-hour-signals",
        )

    def decide(self, candles, as_of_ms):
        return self.engine.decide(candles, as_of_ms)

    def available_profiles(self):
        directory = self.config.path.parent / "strategies"
        return sorted(p.stem for p in directory.glob("*.toml"))
