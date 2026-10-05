import pytest

from intraday.replay_v2.donchian_oos_evaluator import evaluate, load_criteria
from intraday.replay_v2.donchian_oos_config import cases


def receipt():
    items = []
    for name, cfg in cases():
        level = cfg.filter_level
        net = {0:73, 1:203, 2:134, 3:149, 4:159}[level]
        dd = {0:23.8, 1:20.9, 2:10.6, 3:9.9, 4:8.46}[level]
        if cfg.entry_window == 20:
            net = 135
        if cfg.cost_multiplier == 2:
            net = 96
        items.append(dict(variant=name, config=cfg.model_dump(mode='json'), status='complete',
            deterministic_rerun_verified=True, dataset_checksum='a'*64,
            summary=dict(net_pnl=net, net_return_pct=net/10, initial_capital=1000,
            final_equity_known=1000+net, max_drawdown_known_pct=dd,
            exchange_fee_known=38 if level == 4 else 100, slippage_cost_known=23 if level == 4 else 60,
            funding_complete=True, contributions={'spot:ETHUSDT':{'net_pnl':60},'perp:ETHUSDT':{'net_pnl':46}})))
    return dict(window={'start':cases()[0][1].start.isoformat(),'end':cases()[0][1].end.isoformat()}, results=items)


def test_reference_pass_and_threshold_equality():
    r = receipt()
    assert evaluate(r, load_criteria())['verdict'] == 'Pass'
    r['results'][4]['summary']['max_drawdown_known_pct'] = 15
    assert evaluate(r, load_criteria())['mandatory']['G3']['status'] == 'pass'
    r['results'][4]['summary']['max_drawdown_known_pct'] = 15.00001
    assert evaluate(r, load_criteria())['verdict'] == 'Fail'


@pytest.mark.parametrize('case', [4,6])
def test_zero_net_fails_mandatory(case):
    r=receipt(); s=r['results'][case]['summary']
    s.update(net_pnl=0,net_return_pct=0,final_equity_known=1000)
    assert evaluate(r, load_criteria())['verdict'] == 'Fail'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), None])
def test_nonfinite_or_missing_is_invalid(value):
    r=receipt(); r['results'][4]['summary']['net_pnl']=value
    assert evaluate(r, load_criteria())['verdict'] == 'Invalid'


def test_missing_stress_never_uses_static_subtraction():
    r=receipt(); r['results'].pop()
    assert evaluate(r, load_criteria())['verdict'] == 'Invalid'


def test_missing_determinism_or_wrong_config_or_dataset_invalid():
    for key,value in [('deterministic_rerun_verified',False), ('dataset_checksum','b'*64), ('status','limited')]:
        r=receipt(); r['results'][0][key]=value
        assert evaluate(r, load_criteria())['verdict'] == 'Invalid'
    r=receipt(); r['results'][6]['config']['cost_multiplier']=1
    assert evaluate(r, load_criteria())['verdict'] == 'Invalid'


def test_zero_ratio_not_evaluable_and_inconclusive():
    r=receipt()
    for i in (1,2,3,4):
        r['results'][i]['summary']['max_drawdown_known_pct']=0
    r['results'][4]['summary']['contributions']['perp:ETHUSDT']['net_pnl']=-1
    r['results'][5]['summary'].update(net_pnl=200,net_return_pct=20,final_equity_known=1200)
    result=evaluate(r,load_criteria())
    assert result['hypotheses']['H5']['status']=='not_evaluable'
    assert result['verdict']=='Inconclusive'
    assert result['activation_allowed'] is False
