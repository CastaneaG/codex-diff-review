// Dependency-free browser UI. Source text always passes through HTML escaping.
const $ = (selector, root=document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = async (route, options={}) => {
  const response=await fetch('api/'+route,options);
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'请求失败');
  return result;
};
let snapshot,review,files,active,query='',onlyUnread=false,full=false,mode='split',font=13;
let selection=null,editing=null,draft='',drawer=false,reportText='',saveTimer=null;
let saveQueue=Promise.resolve(),saveError=null,collapsed=new Set(),savedVersion=0,pendingSaves=0;
const textLines=text=>(text||'').replace(/\r\n/g,'\n').split('\n');
const stats=f=>`<span class="stats"><b>+${f.additions}</b><i>−${f.deletions}</i></span>`;
const current=()=>files.find(f=>f.id===active);
const visible=()=>files.filter(f=>(!onlyUnread||!review.reviewed.includes(f.id))&&(!query||(f.repo.name+'/'+f.path+'\n'+f.oldText+'\n'+f.newText).toLowerCase().includes(query.toLowerCase())));
const label=c=>c.side==='file'?'文件级建议':`${c.side==='old'?'修改前':'修改后'} L${c.start}${c.end!==c.start?'–'+c.end:''}`;
function notify(message) {let toast=$('#toast');toast.textContent=message;toast.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>toast.hidden=true,3000);}
function saveStatus(message,failed=false){const item=$('#save-state');if(item){item.textContent=message;item.classList.toggle('error',failed);}}
function persist() {
  clearTimeout(saveTimer);
  saveTimer=null;
  const state=structuredClone(review);
  pendingSaves++;
  saveStatus('保存中…');
  saveQueue=saveQueue.then(async()=>{
    if(saveError)throw saveError;
    state.revision=savedVersion;
    const result=await api('review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)});
    savedVersion=result.revision;review.revision=savedVersion;
    saveStatus('已保存到本地');
  }).catch(error=>{saveError=error;saveStatus('保存失败：'+error.message,true);}).finally(()=>pendingSaves--);
  return saveQueue;
}
function changed(){review.completed=false;saveStatus('待保存');clearTimeout(saveTimer);saveTimer=setTimeout(persist,400);}
window.addEventListener('beforeunload',event=>{if(saveTimer||saveError||pendingSaves){event.preventDefault();event.returnValue='';}});
function download(text,name,type='text/markdown;charset=utf-8') {
  const url=URL.createObjectURL(new Blob([text],{type}));
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function copy(text){try{await navigator.clipboard.writeText(text);notify('已复制，可粘贴给 AI')}catch{notify('浏览器不允许复制，请在报告中全选复制或下载。')}}
function shell(){
  const add=files.reduce((n,f)=>n+f.additions,0),del=files.reduce((n,f)=>n+f.deletions,0);
  $('#root').innerHTML=`<div class="app">
    <header class="topbar"><div class="brand">±</div><strong>Diff Review</strong><span class="divider"></span><span class="muted">人工代码审阅</span><span class="spacer"></span><span id="save-state">已保存到本地</span><button id="draft-report">预览报告</button><button id="finish" class="primary">完成审阅并生成报告</button></header>
    <section class="overview"><div><div class="eyebrow">HUMAN REVIEW / LOCAL SNAPSHOT</div><h1>${esc(snapshot.title)}</h1><p>选择代码行 → 写下建议 → 标记文件已阅 → 复制报告给 AI</p></div><div class="metrics"><div><strong>${snapshot.repositories.length}</strong><span>仓库</span></div><div><strong>${files.length}</strong><span>变更文件</span></div><div>${stats({additions:add,deletions:del})}<span>代码行</span></div></div></section>
    <div class="notice"><span class="vtag">快照</span><code>${snapshot.id.slice(0,12)}</code><span>${esc(new Date(snapshot.createdAt).toLocaleString())}</span><span class="spacer"></span><small>只读源码 · 批注保存在本机 · 不自动发送给 AI</small></div>
    <div class="workspace"><aside><div class="sidebar-tools"><label class="search"><span>⌕</span><input id="search" aria-label="搜索文件或代码" placeholder="搜索文件或代码…"></label><div class="filterline"><span id="file-count"></span><label><input id="unread" type="checkbox">仅未阅</label></div></div><nav id="files" aria-label="修改文件"></nav><footer class="sidebar-foot"><div><span>审阅进度</span><b id="progress-text"></b></div><progress id="progress"></progress><small>选行后写建议；Shift 可选择连续多行</small></footer></aside><main id="main"></main></div>
    <div id="toast" role="status" hidden></div>
    <dialog id="report-dialog"><div class="dialog-head"><strong>审阅报告</strong><button id="close-report" aria-label="关闭报告">×</button></div><div id="report-status" class="report-status"></div><textarea id="report-content" aria-label="可复制的审阅报告" readonly></textarea><footer><button id="download-report">下载 Markdown</button><button id="copy-report" class="primary">复制报告给 AI</button></footer></dialog>
    </div>`;
  $('#search').oninput=e=>{query=e.target.value;chooseVisible();renderSidebar();renderMain();};
  $('#unread').onchange=e=>{onlyUnread=e.target.checked;chooseVisible();renderSidebar();renderMain();};
  $('#draft-report').onclick=()=>showReport(false);
  $('#finish').onclick=()=>showReport(true);
  $('#close-report').onclick=()=>$('#report-dialog').close();
  $('#copy-report').onclick=()=>copy(reportText);
  $('#download-report').onclick=()=>download(reportText,'diff-review-'+snapshot.id.slice(0,12)+'.md');
  renderSidebar();renderMain();
}
function chooseVisible(){const list=visible();if(!list.some(f=>f.id===active)){active=list[0]?.id;selection=null;draft='';editing=null;}}
function renderSidebar(){
  const list=visible();
  $('#file-count').textContent=`${list.length} 个文件`;
  $('#files').innerHTML=snapshot.repositories.map(repo=>{
    const group=list.filter(f=>f.repo.id===repo.id);if(!group.length)return '';
    return `<section class="repo"><button class="repo-title" data-repo="${repo.id}" aria-expanded="${!collapsed.has(repo.id)}"><span>${collapsed.has(repo.id)?'›':'⌄'}</span><strong>${esc(repo.name)}</strong><span class="count">${group.length}</span></button>${collapsed.has(repo.id)?'':group.map(f=>{
      const n=review.comments.filter(c=>c.fileId===f.id).length;
      return `<button class="file-item ${active===f.id?'selected':''}" data-file="${f.id}" title="${esc(f.path)}"><span class="status ${f.status}">${review.reviewed.includes(f.id)?'✓':f.status}</span><span class="filename"><strong>${esc(f.path.split('/').pop())}</strong><small>${esc(f.path.includes('/')?f.path.slice(0,f.path.lastIndexOf('/')):'/')}</small></span>${n?`<span class="comment-count">${n}</span>`:''}<span class="tiny-stats">+${f.additions}<br><i>−${f.deletions}</i></span></button>`;
    }).join('')}</section>`;
  }).join('');
  $('#files').onclick=e=>{
    const file=e.target.closest('[data-file]'),repo=e.target.closest('[data-repo]');
    if(file){active=file.dataset.file;selection=null;draft='';editing=null;renderSidebar();renderMain();}
    else if(repo){const id=repo.dataset.repo;collapsed.has(id)?collapsed.delete(id):collapsed.add(id);renderSidebar();}
  };
  $('#progress-text').textContent=`${review.reviewed.length} / ${files.length}`;
  $('#progress').max=files.length;$('#progress').value=review.reviewed.length;
  $('#finish').disabled=review.reviewed.length!==files.length||!!saveError;
  $('#finish').title=review.reviewed.length!==files.length?'请逐个检查并标记所有文件已阅':'生成完整审阅报告';
}
function renderMain(){
  const f=current();
  if(!f){$('#main').innerHTML='<div class="empty"><strong>没有匹配的文件</strong><p>调整搜索条件或关闭“仅未阅”。</p></div>';return;}
  const list=visible(),index=list.findIndex(x=>x.id===active);
  $('#main').innerHTML=`<div class="file-heading"><div class="file-title"><span class="status ${f.status}">${f.status}</span><strong title="${esc(f.path)}">${esc(f.path)}</strong>${stats(f)}</div><div class="file-meta"><span>${esc(f.repo.name)}</span><span>·</span><code>${f.repo.base.slice(0,12)}</code><span>→ ${esc(f.repo.target==='worktree'?'工作区快照':f.repo.target==='index'?'暂存区快照':f.repo.target.slice(0,12))}</span><span class="spacer"></span><button id="prev" aria-label="上一个文件" ${index<=0?'disabled':''}>↑</button><span>${index+1} / ${list.length}</span><button id="next" aria-label="下一个文件" ${index===list.length-1?'disabled':''}>↓</button></div></div>
    <div class="toolbar"><div class="segmented"><button data-mode="split" class="${mode==='split'?'on':''}">左右分栏</button><button data-mode="unified" class="${mode==='unified'?'on':''}">统一视图</button></div><div class="segmented"><button data-full="false" class="${!full?'on':''}">仅改动</button><button data-full="true" class="${full?'on':''}">完整文件</button></div><button id="smaller" aria-label="减小字号">A−</button><button id="larger" aria-label="增大字号">A+</button><span class="spacer"></span><button id="file-comment">文件建议</button><button id="toggle-comments">批注 ${review.comments.filter(c=>c.fileId===f.id).length}</button><label class="review-toggle"><input id="reviewed" type="checkbox" ${review.reviewed.includes(f.id)?'checked':''}>已阅</label></div>
    <div class="review-body ${drawer?'with-drawer':''}"><div class="diff-container" id="diff"></div><section id="comments" class="comment-drawer" ${drawer?'':'hidden'}></section></div><div class="bottomline"><span>${full?'完整文件':'改动及相邻上下文'} · 点击行号写建议，Shift 选择范围</span><span>${f.hunks.length} 个改动区块</span></div>`;
  $('#prev').onclick=()=>navigate(-1);$('#next').onclick=()=>navigate(1);
  document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{mode=b.dataset.mode;renderMain();});
  document.querySelectorAll('[data-full]').forEach(b=>b.onclick=()=>{full=b.dataset.full==='true';renderMain();});
  $('#smaller').onclick=()=>{font=Math.max(11,font-1);renderDiff();};
  $('#larger').onclick=()=>{font=Math.min(18,font+1);renderDiff();};
  $('#reviewed').onchange=e=>{review.reviewed=e.target.checked?[...new Set([...review.reviewed,f.id])]:review.reviewed.filter(id=>id!==f.id);changed();chooseVisible();renderSidebar();renderMain();};
  $('#toggle-comments').onclick=()=>{drawer=!drawer;renderMain();};
  $('#file-comment').onclick=()=>{selection={side:'file',start:null,end:null};editing=null;draft='';drawer=true;renderMain();$('#comment-body')?.focus();};
  renderDiff();renderComments();
}
function navigate(offset){const list=visible(),i=list.findIndex(f=>f.id===active);if(list[i+offset]){active=list[i+offset].id;selection=null;editing=null;draft='';renderSidebar();renderMain();}}
function pairRows(rows){
  const result=[];let removed=[],added=[];
  const flush=()=>{for(let i=0;i<Math.max(removed.length,added.length);i++)result.push({left:removed[i],right:added[i]});removed=[];added=[];};
  for(const r of rows){
    if(r.kind==='hunk'){flush();result.push(r);}
    else if(r.kind==='context'){flush();result.push({left:r,right:r});}
    else if(r.kind==='deletion'){if(added.length)flush();removed.push(r);}
    else added.push(r);
  }
  flush();return result;
}
function displayRows(f){return full?f.rows:f.hunks.flatMap(h=>[{kind:'hunk',text:`@@ -${h.oldStart},${h.oldCount} +${h.newStart},${h.newCount} @@`},...h.rows]);}
function marked(side,n){return selection&&selection.side===side&&n>=selection.start&&n<=selection.end;}
function annotated(side,n){return review.comments.some(c=>c.fileId===active&&c.side===side&&n>=c.start&&n<=c.end);}
function number(side,n){return n===null||n===undefined?'<span class="number"></span>':`<button class="number ${annotated(side,n)?'annotated':''}" data-side="${side}" data-line="${n}" aria-label="${side==='old'?'修改前':'修改后'}第 ${n} 行">${n}</button>`;}
function code(r){return `<code class="code" data-kind="${r?.kind||'empty'}" data-text="${esc(r?.text||'')}">${esc(r?.text||' ')}</code>`;}
function renderDiff(){
  const f=current(),container=$('#diff');if(!f)return;
  container.className='diff-container font-'+font;
  if(f.warning){container.innerHTML=`<div class="binary"><strong>此文件不展开文本差异</strong><p>${esc(f.warning)}</p><p>请在合适的工具中检查后，再标记已阅；仍可添加文件级建议。</p></div>`;return;}
  const rows=displayRows(f);
  const metadata=f.modeChanged?`<div class="mode-note">文件模式 ${esc(f.oldMode)} → ${esc(f.newMode)}</div>`:'';
  if(mode==='split'){
    const paired=pairRows(rows);
    const pane=(side,key)=>`<div class="pane ${key}"><div class="pane-content">${paired.map(r=>r.kind==='hunk'?`<div class="hunk">${esc(r.text)}</div>`:`<div class="line ${r[key]?.kind||'empty'} ${marked(side,r[key]?.[side])?'selected-line':''}">${number(side,r[key]?.[side])}${code(r[key])}</div>`).join('')}</div></div>`;
    container.innerHTML=`<div class="diff-scroll"><div class="diff-head"><div>Base · 修改前 <code>${f.repo.base.slice(0,12)}</code></div><div>快照 · 修改后</div></div>${metadata}<div class="diff-body">${pane('old','left')}${pane('new','right')}</div>${!rows.length?`<div class="mode-note">${esc(f.note||'空文件')}</div>`:''}</div>`;
  }else{
    container.innerHTML=`<div class="diff-scroll"><div class="single-heading">Base → 当前快照 · 统一视图</div>${metadata}<div class="unified-pane">${rows.map(r=>r.kind==='hunk'?`<div class="hunk">${esc(r.text)}</div>`:`<div class="line unified-line ${r.kind} ${marked('old',r.old)||marked('new',r.new)?'selected-line':''}">${number('old',r.old)}${number('new',r.new)}${code(r)}</div>`).join('')}</div>${!rows.length?`<div class="mode-note">${esc(f.note||'空文件')}</div>`:''}</div>`;
  }
  container.onclick=e=>{const target=e.target.closest('[data-line]');if(!target)return;const n=Number(target.dataset.line),side=target.dataset.side;
    if(e.shiftKey&&selection?.side===side){selection={side,start:Math.min(selection.anchor??selection.start,n),end:Math.max(selection.anchor??selection.start,n),anchor:selection.anchor??selection.start};}
    else{selection={side,start:n,end:n,anchor:n};editing=null;}
    drawer=true;
    // Keep scroll positions while highlighting a selection.
    const scroll=$('.diff-scroll').scrollTop;
    const horizontal=Array.from(container.querySelectorAll('.pane,.unified-pane')).map(p=>p.scrollLeft);
    $('.review-body').classList.add('with-drawer');$('#comments').hidden=false;
    renderDiff();renderComments();$('.diff-scroll').scrollTop=scroll;
    container.querySelectorAll('.pane,.unified-pane').forEach((p,i)=>p.scrollLeft=horizontal[i]||0);
  };
  container.querySelectorAll('.pane,.unified-pane').forEach(p=>p.oncopy=copySelection);
}
function copySelection(e){
  const selected=window.getSelection();if(!selected||selected.isCollapsed||selected.rangeCount!==1)return;
  const range=selected.getRangeAt(0),closest=n=>(n instanceof Element?n:n?.parentElement)?.closest('.code');
  const start=closest(range.startContainer),end=closest(range.endContainer);
  if(!start||!end||!e.currentTarget.contains(start)||!e.currentTarget.contains(end))return;
  const lines=Array.from(e.currentTarget.querySelectorAll('.code')).filter(c=>c.dataset.kind!=='empty'&&range.intersectsNode(c)).map(c=>{
    if(c!==start&&c!==end)return c.dataset.text;
    const part=document.createRange();part.selectNodeContents(c);if(c===start)part.setStart(range.startContainer,range.startOffset);if(c===end)part.setEnd(range.endContainer,range.endOffset);return part.toString();
  });e.preventDefault();e.clipboardData.setData('text/plain',lines.join('\n'));
}
function renderComments(){
  const panel=$('#comments');if(!panel)return;
  const comments=review.comments.filter(c=>c.fileId===active);
  panel.innerHTML=`<div class="comment-head"><strong>审阅建议</strong><button id="close-comments" aria-label="关闭批注">×</button></div>
    ${selection?`<form id="comment-form"><div class="anchor-label">${esc(label(selection))}</div><textarea id="comment-body" aria-label="审阅建议内容" maxlength="10000" placeholder="描述问题和希望如何修改…">${esc(draft)}</textarea><div class="comment-actions"><button type="button" id="cancel-comment">取消</button><button class="primary" type="submit">${editing?'更新建议':'保存建议'}</button></div></form>`:'<p class="comment-help">点击代码行号，或按住 Shift 选择连续行；也可以添加文件级建议。</p>'}
    <div class="comments-list">${comments.map(c=>`<article class="comment"><div class="comment-anchor">${esc(label(c))}</div><p>${esc(c.body)}</p><div><button data-edit="${esc(c.id)}">编辑</button><button data-delete="${esc(c.id)}">删除</button></div></article>`).join('')||'<p class="muted">此文件暂无建议</p>'}</div>`;
  $('#close-comments').onclick=()=>{drawer=false;renderMain();};
  if(selection){$('#comment-body').oninput=e=>draft=e.target.value;$('#cancel-comment').onclick=()=>{selection=null;draft='';editing=null;renderDiff();renderComments();};
    $('#comment-form').onsubmit=e=>{e.preventDefault();if(!draft.trim()){notify('请填写建议');return;}
      const c={id:editing||crypto.randomUUID(),fileId:active,side:selection.side,start:selection.start,end:selection.end,body:draft};
      const i=review.comments.findIndex(x=>x.id===editing);if(i>=0)review.comments[i]=c;else review.comments.push(c);
      selection=null;editing=null;draft='';changed();renderSidebar();renderMain();persist();};}
  panel.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const c=comments.find(x=>x.id===b.dataset.edit);selection={side:c.side,start:c.start,end:c.end};draft=c.body;editing=c.id;renderDiff();renderComments();$('#comment-body').focus();});
  panel.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{review.comments=review.comments.filter(c=>c.id!==b.dataset.delete);changed();renderSidebar();renderMain();persist();});
}
async function showReport(complete){
  if(selection&&draft.trim()){notify('请先保存或取消正在编辑的建议');return;}
  if(complete&&review.reviewed.length!==files.length){notify('请先标记所有文件已阅');return;}
  // The summary editor lives in the report dialog and saves before export.
  $('#report-status').innerHTML=`<strong>${complete?'完整审阅':'报告预览（未完成审阅）'}</strong><span>已阅 ${review.reviewed.length}/${files.length} · ${review.comments.length} 条建议</span><label>结论<select id="decision"><option value="changes">请按建议修改</option><option value="approve">本快照审阅通过</option><option value="discuss">需要进一步讨论</option></select></label><label>整体意见<textarea id="summary" maxlength="20000" placeholder="可填写整体修改方向…"></textarea></label><button id="regenerate">更新报告</button>`;
  $('#decision').value=review.decision;$('#summary').value=review.summary;
  $('#decision').onchange=e=>{review.decision=e.target.value;changed();};
  $('#summary').oninput=e=>{review.summary=e.target.value;changed();};
  async function regenerate(){
    review.completed=complete;await persist();
    if(saveError){notify('保存失败，请重载页面核对审阅状态。');return false;}
    const result=await api('report');reportText=result.markdown;$('#report-content').value=reportText;return true;
  }
  $('#regenerate').onclick=regenerate;
  $('#copy-report').onclick=async()=>{if(await regenerate())await copy(reportText);};
  $('#download-report').onclick=async()=>{if(await regenerate())download(reportText,'diff-review-'+snapshot.id.slice(0,12)+'.md');};
  try{if(await regenerate())$('#report-dialog').showModal();}catch(e){notify(e.message);}
}
try{
  [snapshot,review]=await Promise.all([api('snapshot'),api('review')]);savedVersion=review.revision;
  files=snapshot.repositories.flatMap(repo=>repo.files.map(f=>({...f,repo})));
  active=files[0]?.id;shell();
}catch(error){$('#root').innerHTML=`<div class="loading"><h1>无法加载审阅</h1><p>${esc(error.message)}</p><p>请检查本地服务是否运行，或使用 resume 命令重新打开会话。</p></div>`;}
