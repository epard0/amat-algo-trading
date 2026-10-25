"""Configurable research entry point for the default signed ADX extension.

This module has no venue transport, credentials, order API or deployment path.
"""
import json
from pathlib import Path
from autoresearch.crypto_signed_adx import signed_plan, fresh_signed_plan
from autoresearch.crypto_signed_replay import replay_signed


def load_config(path):
    config=json.loads(Path(path).read_text())
    if config['scope']!='research_only':raise ValueError('signed ADX extension is research only')
    if config['leverage'] not in ('1','2','max'):raise ValueError('unsupported leverage')
    if config['loss_guard_fraction']!=.35:raise ValueError('default guard must remain unchanged')
    if set(config['directions'])!={'KXDOGEPERP','KXSOLPERP'}:raise ValueError('default assets required')
    if any(m not in ('long','short','both') for m in config['directions'].values()):raise ValueError('unknown direction mode')
    return config


def replay_configuration(config,frames,data,instruments,metadata,cost_curves,dates,cost_case='median'):
    plans={s:fresh_signed_plan(*signed_plan(frames[s],s.removeprefix('KX').removesuffix('PERP'),mode),data[s]['index'],dates)
           for s,mode in config['directions'].items()}
    return replay_signed(data,plans,instruments,metadata,cost_curves,dates,config['leverage'],cost_case,
                         capital=config.get('capital',10000),guard_fraction=config['loss_guard_fraction'])
