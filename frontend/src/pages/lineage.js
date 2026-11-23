import { badge, lead, section, table } from '../components/common/ui.js';
import { runNames } from '../constants/domain.js';
import { session } from '../stores/session.js';
import { workspace } from '../stores/workspace.js';
import { escape as e, option, period } from '../utils/html.js';

function familyName(code) {
  return workspace.catalog.instruments.find(item => item.code === code)?.name || code;
}

function hallName(code) {
  return workspace.catalog.halls.find(hall => hall.code === code)?.name || code;
}

function originCell(row) {
  const fixed = row.guaranteed_origins.map(item => `${e(hallName(item.hall))} <strong>${item.quantity}</strong>`).join('，') || '—';
  const unknown = row.unknown_quantity
    ? `<span class="badge red">来源不明 ${row.unknown_quantity}</span>` : '<span class="badge green">全部可溯</span>';
  return `<td>${fixed}<div style="margin-top:5px">${unknown}</div></td>`;
}

function receiptRows(traced) {
  return traced.receipts.map(row => `<tr>
    <th scope="row"><span class="code">${e(row.reference)}</span></th>
    <td class="nowrap">${e(row.effective_on)}</td>
    <td>${e(hallName(row.source))}<span class="arrow">→</span>${e(hallName(row.destination))}</td>
    <td class="number">${row.quantity}</td>
    ${originCell(row)}
    <td>${row.incoming.length ? row.incoming.map(item =>
      `<span class="code">${e(item.reference)}</span> <span class="number">${item.min}~${item.max}</span>${item.fixed ? ' ' + badge('固定', 'green') : ''}`).join('<br>') : '—（本单起算）'}</td>
    </tr>`).join('');
}

function linkRows(traced) {
  if (!traced.links.length) return '';
  const rows = traced.links.map(link => `<tr>
    <th scope="row" class="code">${e(link.up)}</th><td class="code">${e(link.down)}</td>
    <td class="number">${link.min} ~ ${link.max}</td>
    <td>${link.fixed ? badge('件次固定', 'green') : badge('并列可变', 'red')}</td></tr>`).join('');
  return section('接续边（沿签领编号）', '区间表示该接续在所有最优匹配中的最小到最大件次；固定边的来源可继续上溯。',
    table(['上游签领', '下游签领', '接续件次区间', '确定性'], rows, ''));
}

export function lineagePage(familyCode = '') {
  const report = workspace.report;
  if (!report) return lead('转借溯源', '沿签领编号对账器材最早来源馆。') +
    '<div class="empty">没有可阅报告。分析员可从透视方案提交计算，复核发布后供各馆查阅。</div>';
  const { result } = report;
  const picker = `<label class="filter-label">选择报告<select id="lineage-report">${workspace.runs.map(row =>
    option(row.id, `${row.series} v${row.version} · #${row.id}`, report.id)).join('')}</select></label>`;
  const heading = lead('转借溯源', '截至方案末日的冲销先排除原单，再在窗口同族签领间求接续件次最大的接法。', picker);
  if (!result) return heading + `<div class="empty" ${report.status === 'queued' ? 'role="status"' : ''}>
    ${report.status === 'queued' ? '后台正在计算，完成后即可沿签领编号对账。' : e(report.error || '核验失败，暂无溯源结果。')}</div>`;

  const lineage = result.lineage;
  if (!lineage) return heading +
    '<div class="empty">这份运行在溯源功能上线前计算，结果中没有来源对账。请分析员用同一方案版本重新运行透视，新运行会在快照冻结后生成转借溯源。</div>';
  const codes = workspace.catalog.instruments.map(item => item.code).filter(code => lineage.families[code]);
  const selected = familyCode && codes.includes(familyCode) ? familyCode : '';
  const shown = selected ? [selected] : codes;
  const stats = `<dl class="stats">
    <div><dt>有效签领件数</dt><dd>${lineage.active_quantity}<small> 件</small></dd></div>
    <div><dt>全窗最大接续件次</dt><dd>${lineage.chained_quantity}<small> 件</small></dd></div>
    <div><dt>来源不明</dt><dd class="${lineage.unknown_quantity ? '' : 'positive'}">${lineage.unknown_quantity}<small> 件</small></dd></div>
    <div><dt>冲销排除原单</dt><dd>${lineage.excluded_reversed.length}<small> 张</small></dd></div></dl>`;
  const filter = section('溯源口径', '同日签领不猜先后；接不上的件次从该单出借馆起算，并列最优接法改变来源馆时计为来源不明。',
    `<div class="lineage">
      <p>报告周期 <span class="code">${period(report.start_on, report.end_on)}</span> · ${badge(runNames[report.status], report.status === 'done' ? 'green' : 'red')}
      ${report.published_at ? badge('已发布', 'green') : badge('待复核')}</p>
      <label class="filter-label" style="display:flex;max-width:340px">器材族<select id="lineage-family">
        ${option('', '全部器材族', selected)}${codes.map(code => option(code, familyName(code), selected)).join('')}</select></label>
      <p>已排除的已冲销原单：<span class="code">${lineage.excluded_reversed.map(e).join('、') || '无'}</span></p>
      ${lineage.prior_reversal_refs.length ? `<p>窗口前补录、末日前生效的冲销：<span class="code">${lineage.prior_reversal_refs.map(e).join('、')}</span>（仅影响本报告之后的新运行）</p>` : ''}
    </div>`);
  const sections = shown.map(code => {
    const traced = lineage.families[code];
    const unit = workspace.catalog.instruments.find(item => item.code === code)?.unit || '';
    return section(`${familyName(code)} · 签领来源对账`,
      `有效 ${traced.active_quantity} ${unit}　接续 ${traced.chained_quantity} ${unit}　来源不明 ${traced.unknown_quantity} ${unit}`,
      table(['签领编号', '生效日', '出借 → 入借', '数量', '可固定来源馆 / 不明件数', '上游接续（件次区间）'],
        receiptRows(traced), '该器材族在窗口内没有未冲销签领。') + linkRows(traced));
  }).join('');
  return heading + stats + filter + sections +
    (session.user.role !== 'observer' ? '<p class="lineage-note">完整溯源明细随报告 CSV 导出，可按签领编号复核。</p>' : '');
}

export function bindLineage() {
  document.querySelector('#lineage-report')?.addEventListener('change', event => {
    location.hash = `lineage/${event.target.value}`;
  });
  document.querySelector('#lineage-family')?.addEventListener('change', event => {
    const runId = workspace.report?.id ?? '';
    location.hash = `lineage/${runId}${event.target.value ? `?f=${event.target.value}` : ''}`;
  });
}
