"""Current default decision parity using the native backtest scheduler."""
from pathlib import Path
from decimal import Decimal
import pytest
from amat.config.loading import load_strategy_config
from amat.data.synthetic import generate_panel
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.positions import Position
from amat.domain.types import Side
from amat.planning import build_plan
from amat.planning.scheduled import plan_scheduled_decision

@pytest.mark.parametrize('cost',[2,6,10])
@pytest.mark.parametrize('seed',[13,41])
def test_current_default_planner_matches_native_instructions(cost,seed):
    resolved=load_strategy_config(Path(__file__).resolve().parents[2]/'configs/strategy/amat_group_selection_volatility.toml')
    strategy=resolved.strategy
    strategy=strategy.model_copy(update={'adaptive_rotation':strategy.adaptive_rotation.model_copy(update={'switch_cost_bps':float(cost)})})
    panel=generate_panel(strategy.to_universe(),sessions=340,seed=seed,trend_strength=.5)
    marks=panel.marks_at(len(panel)-1);session=panel.sessions[-1]
    for holdings in ({},{str(panel.symbols[0]):Decimal('10.25'),str(panel.symbols[4]):Decimal('5')}):
        positions={s:Position(symbol=s,quantity=holdings[str(s)],mark=marks[s],as_of_session=session) for s in panel.symbols if str(s) in holdings}
        ledger=LedgerSnapshot(session,Decimal('100000')-sum((p.quantity*p.mark for p in positions.values()),Decimal(0)),positions)
        plan=build_plan(strategy,panel,strategy.to_universe(),engine='v1-frozen',current_positions=holdings,account_equity=ledger.equity,config_sha256=resolved.content_sha256,snapshot_id='synthetic-parity-only',code_revision='test')
        native=plan_scheduled_decision(strategy,panel,ledger=ledger,schedule=[],execution_session=plan.earliest_execution_session,one_way_bps=cost,entry_basis={},embargo_until={},pending={})
        assert sorted((i.symbol,i.side,i.quantity) for i in plan.intents)==sorted((str(i.symbol),'buy' if i.side==Side.BUY else 'sell',i.quantity) for i in native.instructions)
