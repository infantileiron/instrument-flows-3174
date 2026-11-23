"""转借溯源算法的离线校验：python -I backend/tests/test_lineage.py

不依赖数据库与签名密钥；用桩替换验签函数，覆盖演示数据接续与"来源不明"并列接法。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

crypto_stub = types.ModuleType('src.utils.crypto')
crypto_stub.signed = lambda row: True
sys.modules.setdefault('src.utils.crypto', crypto_stub)

from src.services import lineage as lineage_module  # noqa: E402


def loan(receipt_id, reference, day, source, destination, instrument, quantity):
    return {'id': receipt_id, 'reference': reference, 'kind': 'loan', 'original_id': None,
            'source': source, 'destination': destination, 'instrument': instrument,
            'quantity': quantity, 'effective_on': f'2026-11-{day:02d}', 'signature': 'x'}


def reversal(receipt_id, reference, day, original_id, instrument):
    return {'id': receipt_id, 'reference': reference, 'kind': 'reversal',
            'original_id': original_id, 'source': 'CT', 'destination': 'YH',
            'instrument': instrument, 'quantity': 1, 'effective_on': f'2026-11-{day:02d}',
            'signature': 'x'}


def snapshot_with(loans, reversals=None, prior_reversals=None, instruments=('SHENG',)):
    return {
        'halls': [{'code': 'CT'}, {'code': 'YH'}, {'code': 'ZS'}],
        'instruments': [{'code': code} for code in instruments],
        'receipts': loans + (reversals or []),
        'originals': [],
        'prior_reversals': prior_reversals or [],
    }


def by_ref(result, family, reference):
    return next(item for item in result['families'][family]['receipts']
                if item['reference'] == reference)


def test_demo_chains():
    loans = [
        loan(1, 'CT-1102-01', 2, 'CT', 'YH', 'SHENG', 8),
        loan(2, 'CT-1103-02', 3, 'CT', 'YH', 'SHENG', 5),       # 被窗内冲销，排除
        loan(3, 'YH-1104-01', 4, 'YH', 'CT', 'SHENG', 3),
        loan(4, 'YH-1110-01', 10, 'YH', 'ZS', 'SHENG', 3),
        loan(5, 'YH-1105-01', 5, 'YH', 'ZS', 'DRUM', 6),
        loan(6, 'ZS-1106-01', 6, 'ZS', 'YH', 'DRUM', 2),
        loan(7, 'ZS-1112-01', 12, 'ZS', 'CT', 'DRUM', 2),
        loan(8, 'ZS-1107-01', 7, 'ZS', 'CT', 'GONG', 4),
        loan(9, 'CT-1108-01', 8, 'CT', 'ZS', 'GONG', 1),
        loan(10, 'CT-1109-01', 9, 'CT', 'YH', 'GONG', 2),
        loan(11, 'YH-1113-01', 13, 'YH', 'CT', 'GONG', 2),
    ]
    reversals = [reversal(20, 'CX-1111-01', 11, 2, 'SHENG')]
    snapshot = snapshot_with(loans, reversals, instruments=('SHENG', 'DRUM', 'GONG'))
    result = lineage_module.trace_lineage(snapshot)

    assert result['excluded_reversed'] == ['CT-1103-02']
    assert result['active_quantity'] == 33
    assert result['chained_quantity'] == 15
    assert result['unknown_quantity'] == 0

    sheng = result['families']['SHENG']
    assert sheng['chained_quantity'] == 6
    assert by_ref(result, 'SHENG', 'YH-1104-01')['guaranteed_origins'] == [
        {'hall': 'CT', 'quantity': 3}]
    assert by_ref(result, 'SHENG', 'YH-1110-01')['guaranteed_origins'] == [
        {'hall': 'CT', 'quantity': 3}]
    assert by_ref(result, 'SHENG', 'CT-1102-01')['guaranteed_origins'] == [
        {'hall': 'CT', 'quantity': 8}]

    drum = result['families']['DRUM']
    assert drum['chained_quantity'] == 4
    assert by_ref(result, 'DRUM', 'ZS-1106-01')['guaranteed_origins'] == [
        {'hall': 'YH', 'quantity': 2}]
    assert by_ref(result, 'DRUM', 'ZS-1112-01')['guaranteed_origins'] == [
        {'hall': 'YH', 'quantity': 2}]

    gong = result['families']['GONG']
    assert gong['chained_quantity'] == 5
    final = by_ref(result, 'GONG', 'YH-1113-01')
    # 经 CT-1109-01 固定接续，而 CT-1109-01 的两件已全部确定源自 ZS，可继续传递而非判不明。
    assert final['guaranteed_origins'] == [{'hall': 'ZS', 'quantity': 2}]
    assert final['unknown_quantity'] == 0
    print('demo chains OK')


def test_prior_reversal_excludes_loan():
    loans = [loan(1, 'CT-1102-01', 2, 'CT', 'YH', 'SHENG', 4),
             loan(2, 'YH-1104-01', 4, 'YH', 'ZS', 'SHENG', 2)]
    prior = [reversal(30, 'CX-1031-01', 1, 1, 'SHENG')]
    result = lineage_module.trace_lineage(snapshot_with(loans, prior_reversals=prior))
    assert result['excluded_reversed'] == ['CT-1102-01']
    assert result['prior_reversal_refs'] == ['CX-1031-01']
    assert result['active_quantity'] == 2
    assert result['chained_quantity'] == 0
    print('prior reversal OK')


def test_same_day_not_chained():
    loans = [loan(1, 'CT-1105-01', 5, 'CT', 'YH', 'SHENG', 3),
             loan(2, 'YH-1105-02', 5, 'YH', 'ZS', 'SHENG', 3)]
    result = lineage_module.trace_lineage(snapshot_with(loans))
    assert result['chained_quantity'] == 0
    assert result['families']['SHENG']['links'] == []
    print('same day OK')


def test_ambiguous_origin_marked():
    # CT->YH 与 ZS->YH 各 2 件（11-02），YH 再向 ZS 转出 3 件（11-06）。
    # 最大接续 3 件：两种并列接法让 YH-1106-01 的来源馆组合不同（CT/ZS 取舍）。
    loans = [
        loan(1, 'CT-1102-01', 2, 'CT', 'YH', 'SHENG', 2),
        loan(2, 'ZS-1102-01', 2, 'ZS', 'YH', 'SHENG', 2),
        loan(3, 'YH-1106-01', 6, 'YH', 'ZS', 'SHENG', 3),
    ]
    result = lineage_module.trace_lineage(snapshot_with(loans))
    assert result['chained_quantity'] == 3
    target = by_ref(result, 'SHENG', 'YH-1106-01')
    assert target['origins'] == ['CT', 'YH', 'ZS']
    # 两条入边各至少 1 件：CT、ZS 各固定 1 件，第 3 件随并列接法改变来源馆，判来源不明。
    assert target['unknown_quantity'] == 1
    assert result['unknown_quantity'] == 1
    assert {'hall': 'CT', 'quantity': 1} in target['guaranteed_origins']
    assert {'hall': 'ZS', 'quantity': 1} in target['guaranteed_origins']
    links = result['families']['SHENG']['links']
    assert all(not link['fixed'] for link in links)
    print('ambiguous origin OK')


def test_partial_guarantee_inside_ambiguity():
    # YH 共持有 5 件（CT 3 + ZS 2），向 ZS 转 4 件：CT 边至少 2 件、ZS 边至少 1 件，余 1 件来源不明。
    loans = [
        loan(1, 'CT-1102-01', 2, 'CT', 'YH', 'SHENG', 3),
        loan(2, 'ZS-1102-01', 2, 'ZS', 'YH', 'SHENG', 2),
        loan(3, 'YH-1106-01', 6, 'YH', 'ZS', 'SHENG', 4),
    ]
    result = lineage_module.trace_lineage(snapshot_with(loans))
    target = by_ref(result, 'SHENG', 'YH-1106-01')
    assert target['unknown_quantity'] == 1
    assert {'hall': 'CT', 'quantity': 2} in target['guaranteed_origins']
    assert {'hall': 'ZS', 'quantity': 1} in target['guaranteed_origins']
    print('partial guarantee OK')


def test_split_to_multiple_destinations():
    # 一张签领拆给两个去向，每件在前最多接一次。
    loans = [
        loan(1, 'CT-1102-01', 2, 'CT', 'YH', 'SHENG', 4),
        loan(2, 'YH-1104-01', 4, 'YH', 'CT', 'SHENG', 3),
        loan(3, 'YH-1105-01', 5, 'YH', 'ZS', 'SHENG', 3),
    ]
    result = lineage_module.trace_lineage(snapshot_with(loans))
    assert result['chained_quantity'] == 4
    # 4 件拆给两个去向：两条边各 min=1/max=3，每张下游单只保证 1 件源自 CT，余 2 件来源不明。
    assert by_ref(result, 'SHENG', 'YH-1104-01')['guaranteed_origins'] == [
        {'hall': 'CT', 'quantity': 1}]
    assert by_ref(result, 'SHENG', 'YH-1104-01')['unknown_quantity'] == 2
    assert by_ref(result, 'SHENG', 'YH-1105-01')['guaranteed_origins'] == [
        {'hall': 'CT', 'quantity': 1}]
    assert by_ref(result, 'SHENG', 'YH-1105-01')['unknown_quantity'] == 2
    print('split destinations OK')


def test_split_with_fixed_branch():
    # CT 出 5 件；大分支只能接 3 件，要达到最大接续 5，小分支的 2 件在每个最大匹配里固定。
    loans = [
        loan(1, 'CT-1102-01', 2, 'CT', 'YH', 'SHENG', 5),
        loan(2, 'YH-1104-01', 4, 'YH', 'CT', 'SHENG', 3),
        loan(3, 'YH-1105-01', 5, 'YH', 'ZS', 'SHENG', 2),
    ]
    result = lineage_module.trace_lineage(snapshot_with(loans))
    assert result['chained_quantity'] == 5
    small = by_ref(result, 'SHENG', 'YH-1105-01')
    assert small['guaranteed_origins'] == [{'hall': 'CT', 'quantity': 2}]
    assert small['unknown_quantity'] == 0
    large = by_ref(result, 'SHENG', 'YH-1104-01')
    assert large['guaranteed_origins'] == [{'hall': 'CT', 'quantity': 3}]
    print('split fixed branch OK')


if __name__ == '__main__':
    test_demo_chains()
    test_prior_reversal_excludes_loan()
    test_same_day_not_chained()
    test_ambiguous_origin_marked()
    test_partial_guarantee_inside_ambiguity()
    test_split_to_multiple_destinations()
    test_split_with_fixed_branch()
    print('all lineage tests passed')
