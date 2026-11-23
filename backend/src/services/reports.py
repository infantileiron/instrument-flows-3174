import csv
import io
from pathlib import Path

REPORTS = Path('/reports')


def csv_payload(snapshot, result):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['器材族', '单位', '出借馆', '入借馆', '签领', '冲销', '有效调借', '反向有效调借', '双向相抵', '本方向差额'])
    halls = {item['code']: item['name'] for item in snapshot['halls']}
    instruments = {item['code']: item for item in snapshot['instruments']}
    for item in snapshot['instruments']:
        family = result['family'][item['code']]
        for row in family['directions']:
            source, destination = row['source'], row['destination']
            opposite = family['matrix'][destination][source]
            writer.writerow([item['name'], item['unit'], halls[source], halls[destination], row['loan'], row['reversal'],
                             row['effective'], opposite, max(0, min(row['effective'], opposite)), row['effective'] - opposite])

    lineage = result.get('lineage')
    if lineage:
        writer.writerow([])
        writer.writerow(['转借来源溯源 · 沿签领编号对账（截至方案末日的冲销先排除原单，同日不猜先后）'])
        writer.writerow(['器材族', '签领编号', '生效日', '出借馆', '入借馆', '数量',
                         '可固定来源馆', '来源不明件数', '上游签领（最小区间~最大区间）'])
        for code, traced in lineage['families'].items():
            unit_name = instruments[code]['name']
            for row in traced['receipts']:
                fixed = '；'.join(f"{halls[item['hall']]} {item['quantity']}"
                                  for item in row['guaranteed_origins']) or '—'
                upstream = '；'.join(f"{item['reference']} {item['min']}~{item['max']}"
                                     for item in row['incoming']) or '—'
                writer.writerow([unit_name, row['reference'], row['effective_on'],
                                 halls[row['source']], halls[row['destination']], row['quantity'],
                                 fixed, row['unknown_quantity'], upstream])
        writer.writerow([])
        writer.writerow(['溯源汇总', '有效签领件数', lineage['active_quantity'],
                         '全窗最大接续件次', lineage['chained_quantity'],
                         '来源不明件数', lineage['unknown_quantity']])
        writer.writerow(['已冲销排除原单', '、'.join(lineage['excluded_reversed']) or '—'])
        if lineage['prior_reversal_refs']:
            writer.writerow(['窗口前补录且末日前生效的冲销', '、'.join(lineage['prior_reversal_refs'])])
    return out.getvalue().encode('utf-8-sig')


def report_path(run_id):
    return REPORTS / f'flow-{run_id}.csv'


def save_report(run_id, snapshot, result):
    path = report_path(run_id)
    temp = path.with_suffix('.tmp')
    temp.write_bytes(csv_payload(snapshot, result))
    temp.replace(path)
