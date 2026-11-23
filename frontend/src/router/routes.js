export const links = [
  ['report', '净流报告', '▤'], ['lineage', '转借溯源', '⤳'], ['ledger', '凭据台账', '▥'],
  ['reversals', '冲销核对', '↶'], ['plans', '透视方案', '⊞'], ['catalog', '馆藏名录', '▧'],
];

export function route() {
  const hash = location.hash.slice(1);
  const [path, query = ''] = hash.split('?');
  const [page, selected] = path.split('/');
  const family = new URLSearchParams(query).get('f') || '';
  return {
    page: links.some(([key]) => key === page) ? page : 'report',
    runId: /^\d+$/.test(selected ?? '') ? Number(selected) : null,
    family,
  };
}
