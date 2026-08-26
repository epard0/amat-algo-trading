"""Instruments, risk clusters, and the tradable universe.

Cluster membership is a property of the universe, not of a lookup table
somewhere else, so a symbol can never be sized without a cluster cap applying
to it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from amat.domain.types import SessionDate, Symbol
from amat.errors import ConfigurationError

__all__ = ["Cluster", "Instrument", "Universe"]


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradable ETF and the metadata needed to decide availability.

    Attributes:
        symbol: Normalized ticker.
        cluster: Risk-cluster identifier this instrument belongs to.
        currency: Settlement currency, ISO 4217.
        first_available_session: Earliest session at which both valid price
            history and instrument metadata exist. A symbol may not enter the
            universe before this date.
    """

    symbol: Symbol
    cluster: str
    currency: str
    first_available_session: SessionDate | None = None

    def is_available_on(self, session: SessionDate) -> bool:
        """Whether the instrument may be considered on ``session``."""
        if self.first_available_session is None:
            return True
        return session >= self.first_available_session


@dataclass(frozen=True, slots=True)
class Cluster:
    """A named risk group with a gross-exposure cap."""

    name: str
    gross_cap: float
    symbols: tuple[Symbol, ...]

    def __post_init__(self) -> None:
        if not self.name:
            message = "cluster name must be non-empty"
            raise ConfigurationError(message)
        if not 0.0 < self.gross_cap <= 1.0:
            message = f"cluster {self.name}: gross_cap must be in (0, 1], received {self.gross_cap}"
            raise ConfigurationError(message)
        if not self.symbols:
            message = f"cluster {self.name}: must contain at least one symbol"
            raise ConfigurationError(message)
        if len(set(self.symbols)) != len(self.symbols):
            message = f"cluster {self.name}: duplicate symbols"
            raise ConfigurationError(message)


@dataclass(frozen=True, slots=True)
class Universe:
    """The full tradable set, partitioned into clusters.

    Symbol order is the declaration order of the clusters. It is stable so that
    every downstream matrix (covariance, weights, targets) has a deterministic
    column order.
    """

    clusters: tuple[Cluster, ...]
    instruments: Mapping[Symbol, Instrument]

    def __post_init__(self) -> None:
        # Defensive copy for the same reason as LedgerSnapshot.positions: a
        # frozen dataclass protects the binding, not the mapping behind it, and
        # a caller-retained handle could add a symbol after the exclusivity
        # check has already run.
        object.__setattr__(self, "instruments", MappingProxyType(dict(self.instruments)))

    @classmethod
    def from_clusters(
        cls,
        clusters: Sequence[Cluster],
        *,
        currency: str = "USD",
        first_available: Mapping[Symbol, SessionDate] | None = None,
    ) -> Universe:
        """Build a universe, requiring each symbol to appear in exactly one cluster."""
        if not clusters:
            message = "universe must declare at least one cluster"
            raise ConfigurationError(message)
        seen: dict[Symbol, str] = {}
        instruments: dict[Symbol, Instrument] = {}
        availability = first_available or {}
        for cluster in clusters:
            for symbol in cluster.symbols:
                if symbol in seen:
                    message = (
                        f"symbol {symbol} appears in clusters "
                        f"{seen[symbol]!r} and {cluster.name!r}; membership must be exclusive"
                    )
                    raise ConfigurationError(message)
                seen[symbol] = cluster.name
                instruments[symbol] = Instrument(
                    symbol=symbol,
                    cluster=cluster.name,
                    currency=currency,
                    first_available_session=availability.get(symbol),
                )
        return cls(clusters=tuple(clusters), instruments=instruments)

    @property
    def symbols(self) -> tuple[Symbol, ...]:
        """Deterministic column order for every downstream matrix."""
        return tuple(symbol for cluster in self.clusters for symbol in cluster.symbols)

    def cluster_of(self, symbol: Symbol) -> str:
        """Return the cluster name for ``symbol``."""
        try:
            return self.instruments[symbol].cluster
        except KeyError as exc:
            message = f"symbol {symbol} is not in the universe"
            raise ConfigurationError(message) from exc

    def cluster_caps(self) -> Mapping[str, float]:
        """Gross-exposure cap per cluster name."""
        return {cluster.name: cluster.gross_cap for cluster in self.clusters}

    def available_on(self, session: SessionDate) -> tuple[Symbol, ...]:
        """Symbols whose history and metadata exist by ``session``."""
        return tuple(
            symbol for symbol in self.symbols if self.instruments[symbol].is_available_on(session)
        )

    def __iter__(self) -> Iterator[Symbol]:
        return iter(self.symbols)

    def __len__(self) -> int:
        return len(self.symbols)
