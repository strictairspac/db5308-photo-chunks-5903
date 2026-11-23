import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {topic} from '../constants/project.js';
import {date, el, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

const lookupState = {address:'', results:[], total:0, reportCount:0, hasMore:false, cursor:null};

function lookupPanel() {
  return panel('按块摘要反查', `
    <form id="lookup-form" class="actions">
      <label class="field" style="flex:1"><span>块摘要（SHA-256）</span><input name="address" maxlength="80" required value="${esc(lookupState.address)}" placeholder="64 位十六进制" /></label>
      <button>反查</button>
    </form>
    <p class="note" id="lookup-summary">${esc(lookupSummary())}</p>
    <div id="lookup-results"></div>
    <div class="actions"><button class="secondary" type="button" id="lookup-more" ${lookupState.hasMore?'':'hidden'}>加载更多</button></div>`);
}

const lookupColumns = ['报告编号', '执行', '底片版本', '底片文件', '块序号'];

function lookupRows() {
  return lookupState.results.map(row=>[
    `<a href="#evidence/${row.report_id}">报告 ${row.report_id}</a>`,
    esc(row.run_id),
    esc(row.snapshot_id),
    esc(row.asset),
    esc(row.chunk_index),
  ]);
}

function lookupSummary() {
  if (!lookupState.address) return '输入块摘要后，逐处列出报告编号、底片版本与块序号。';
  if (!lookupState.total) return '当前已出具的报告中没有该块的引用。';
  return `共 ${lookupState.total} 处引用，涉及 ${lookupState.reportCount} 份报告。`;
}

export function evidencePage(state, identity) {
  const current = state.evidence.find(row=>row.id===identity) || state.evidence[0];
  const lookup = lookupPanel();
  if (!current) return heading(topic.modules[3]) + '<p class="empty">执行完成后，可查看、复核和导出证据。</p>' + lookup;
  const {report} = current;
  const metrics = Object.entries(report.metrics).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  const sections = report.sections.map(x=>panel(x.title, entries(x.rows))).join('');
  const review = current.reviewer ? `<p data-testid="reviewed">${esc(current.reviewer)} · ${esc(date(current.reviewed_at))}</p><p>${esc(current.review_note)}</p>`
    : state.session.role==='reviewer'?'<form id="review-form"><label>复核意见<textarea name="note" maxlength="500" required></textarea></label><button>签署复核</button></form>'
    : '<p class="note">待复核员签署</p>';
  const history = state.evidence.map(row=>[`<a href="#evidence/${row.id}">执行 ${row.run_id}</a>`, esc(row.report.summary), esc(row.reviewer || '待复核')]);
  return `${heading(topic.modules[3], report.summary, `<button id="download" data-id="${current.id}">导出证据</button>`)}
    <div class="spread evidence-top"><dl class="metrics">${metrics}</dl><aside class="brief"><h2>复核签署</h2>${review}<details><summary>结果校验码</summary><code class="digest">${esc(current.digest)}</code></details></aside></div>
    ${lookup}
    ${sections}<details><summary>历史证据</summary>${records(['执行','结论','复核'], history)}</details>`;
}

function drawLookup() {
  const container = document.getElementById('lookup-results');
  const summary = document.getElementById('lookup-summary');
  const more = document.getElementById('lookup-more');
  if (!container) return;
  const rows = lookupRows();
  container.innerHTML = rows.length ? records(lookupColumns, rows) : '';
  summary.textContent = lookupSummary();
  if (more) more.hidden = !lookupState.hasMore;
}

async function fetchLookup(api, address, cursor) {
  const query = new URLSearchParams({address, limit:'50'});
  if (cursor) query.set('cursor', cursor);
  return api(`/blocks/occurrences?${query}`);
}

export function bindEvidence(api, update, fail) {
  drawLookup();
  const button = document.getElementById('download');
  if (button) {
    const identity = button.dataset.id;
    button.addEventListener('click', async()=>{
      try {
        const response = await api(`/evidence/${identity}/download`, {response:true});
        const url = URL.createObjectURL(await response.blob());
        const link = el('a');
        link.href = url;
        link.download = `evidence-${identity}.json`;
        link.click();
        setTimeout(()=>URL.revokeObjectURL(url), 1000);
      } catch (error) { fail(error); }
    });
    submit('review-form', async data=>{
      await api(`/evidence/${identity}/review`, {method:'POST', data:{note:data.get('note')}});
      await update();
    }, fail);
  }

  submit('lookup-form', async data=>{
    const address = String(data.get('address') || '').trim();
    try {
      const result = await fetchLookup(api, address, null);
      Object.assign(lookupState, {address, results:result.results, total:result.total,
        reportCount:result.report_count, hasMore:result.has_more, cursor:result.next_cursor});
      drawLookup();
    } catch (error) { fail(error); }
  }, fail);

  document.getElementById('lookup-more')?.addEventListener('click', async event=>{
    event.currentTarget.disabled = true;
    try {
      const result = await fetchLookup(api, lookupState.address, lookupState.cursor);
      lookupState.results.push(...result.results);
      Object.assign(lookupState, {total:result.total, reportCount:result.report_count,
        hasMore:result.has_more, cursor:result.next_cursor});
      drawLookup();
    } catch (error) { fail(error); }
    finally { event.currentTarget.disabled = false; }
  });
}
