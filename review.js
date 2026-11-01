'use strict';
window.createBatchReview = ({node, getData, mutate, refresh, showError}) => {
  const drafts = new Set();
  let signature = '', busy = 0;
  const openItems = new Set(), filters = new Map();
  let source = '', announcement = '';
  const queueRow = (id,finding) => rows('review_queue').find(r=>r.batch_id===id&&r.finding_id===finding);
  const itemsFor = batch => rows('batch_findings').filter(r=>r.batch_id===batch.id).map(r=>rows('findings').find(f=>f.id===r.finding_id)).filter(Boolean).sort((a,b)=>a.path.localeCompare(b.path)||(a.line||0)-(b.line||0)||a.id.localeCompare(b.id,undefined,{numeric:true}));
  const filterFor = id => filters.get(id)||{state:'remaining',text:''};
  const visibleItems = batch => itemsFor(batch).filter(item=>{const f=filterFor(batch.id),r=queueRow(batch.id,item.id);return (f.state==='all'||(f.state==='remaining'?r.state!=='accepted':r.state===f.state))&&[item.id,item.title,item.path,r.review_notes].join(' ').toLowerCase().includes(f.text.toLowerCase());});
  const $ = id => document.getElementById(id);
  const rows = table => getData()[table] || [];
  const completed = id => rows('batch_completions').find(r => r.batch_id === id);
  const prs = id => rows('batch_pull_requests').filter(r => r.batch_id === id);
  const metadata = (id,url) => {
    const row = rows('pull_request_details').find(r => r.batch_id === id && r.url === url);
    return {row, value: row?.metadata_json ? JSON.parse(row.metadata_json) : null};
  };
  const latest = (id,finding) => rows('finding_validations').filter(r => r.batch_id===id && r.finding_id===finding).sort((a,b)=>b.id-a.id)[0];
  const fresh = v => {const {row,value}=metadata(v.batch_id,v.pr_url);return !!value&&!row.error&&value.head_sha===v.revision;};
  const date = value => value ? new Date(value).toLocaleString() : 'Never';
  const accepted = (batchId,findingId) => queueRow(batchId,findingId)?.state==='accepted';
  function link(label,url) {const a=node('a',label);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
  function button(label,action,className) {const b=node('button',label,className);b.type='button';b.addEventListener('click',()=>action(b));return b;}
  function details(label,key) {const d=node('details');d.append(node('summary',label));d.open=openItems.has(key);d.addEventListener('toggle',()=>{if(!d.isConnected)return;if(d.open)openItems.add(key);else openItems.delete(key);});return d;}
  async function act(route,payload,b) {busy++;if(b)b.disabled=true;try {await mutate(route,payload);await refresh();}catch(e){showError(e);}finally{busy--;if(b?.isConnected)b.disabled=false;}}
  function field(label,el) {const wrap=node('label',label,'field');el.setAttribute('aria-label',label);wrap.append(el);return wrap;}
  function input(type,value='') {const el=node('input');el.type=type;el.value=value;return el;}
  function prCard(batch,pr) {
    const {row,value:m}=metadata(batch.id,pr.url),box=node('div',undefined,'review-pr');
    const heading=node('div',undefined,'review-pr-heading');
    heading.append(link(`#${pr.url.split('/').pop()} · ${m?.title||'Pull request'}`,pr.url));
    heading.append(node('span',m?.merged_at?'Merged':m?.state==='closed'?'Closed':m?.draft?'Draft':m?'Open':'Not checked','review-badge'));
    box.append(heading);
    if(m) {
      const stats=node('div',undefined,'pr-stats');
      const comments=link(`${m.comments+m.review_comments} comments`,pr.url+'#discussion_bucket');comments.title=`${m.comments} discussion comments + ${m.review_comments} inline review comments`;
      stats.append(comments,link(`${m.commits} commits`,pr.url+'/commits'),link(`${m.changed_files} files`,pr.url+'/files'),node('span',`+${m.additions}`,'diff-add'),node('span',`−${m.deletions}`,'diff-remove'));
      box.append(stats,node('p',`${m.head_branch} → ${m.base_branch} · ${m.head_sha.slice(0,7)} · Checked ${date(row.fetched_at)}`,'meta'));
    }
    if(row?.error) box.append(node('p','Could not refresh GitHub. '+row.error,'submission-error'));
    if(!m) box.append(node('p','Use Refresh PRs to load GitHub activity.','meta'));
    return box;
  }
  function addPr(batch) {
    const d=details('Add PR…','add-pr-'+batch.id),form=node('form',undefined,'pr-add-form'),url=input('url');url.required=true;url.placeholder='https://github.com/owner/repo/pull/123';url.setAttribute('aria-label',`Add PR to Batch #${batch.id}`);
    const submit=node('button','Add PR');submit.type='submit';form.append(url,submit,button('Cancel',()=>{url.value='';drafts.delete(form);d.open=false;}));d.append(form);
    url.addEventListener('input',()=>{url.value?drafts.add(form):drafts.delete(form);});
    form.addEventListener('submit',async e=>{e.preventDefault();busy++;submit.disabled=true;try{await mutate('/api/batch-pr',{batch_id:batch.id,url:url.value});drafts.delete(form);url.value='';await mutate('/api/refresh-prs',{batch_id:batch.id});await refresh();}catch(err){showError(err);}finally{busy--;submit.disabled=false;}});
    return d;
  }
  async function prompt(batch,item) {
    if(drafts.size){showError(Error('Save or cancel your current edits before preparing validation.'));return;}
    const dialog=node('dialog',undefined,'validation-dialog'),heading=node('div',undefined,'section-heading');
    const title=item?`Re-Review ${item.id} · Batch #${batch.id}`:`Validate Batch #${batch.id}`,label=item?'Re-review prompt':'Validation prompt';
    heading.append(node('h2',title),button('×',()=>dialog.close()));dialog.append(heading,node('p',item?'Copy this prompt and start an agent in a session you choose. It re-reviews this finding only, using SQLite for the finding, previous checks, and your instructions, then saves a new recommendation and evidence. Reload to review it here. No agent starts from this button.':'Copy this prompt and start an agent in a session you choose. It reads the whole batch from SQLite and saves recommendations, brief notes, and useful screenshots. Reload to review them here. No agent starts from this button.','section-note'));
    const area=node('textarea',undefined,'agent-prompt');area.readOnly=true;area.rows=16;area.setAttribute('aria-label',label);area.value='Preparing prompt…';dialog.append(area);
    const status=node('p','','meta'),copy=button(item?'Copy re-review prompt':'Copy validation prompt',async()=>{try{await navigator.clipboard.writeText(area.value);status.textContent='Copied · Start the agent yourself';}catch(e){status.textContent='Select and copy the prompt above.';}},'primary');copy.disabled=true;dialog.append(copy,status);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
    try{area.value=(await mutate(item?'/api/re-review-prompt':'/api/validation-prompt',item?{batch_id:batch.id,finding_id:item.id}:{batch_id:batch.id})).prompt;copy.disabled=false;}catch(e){area.value=e.message;}
  }
  function evidence(v) {
    const gallery=node('div',undefined,'evidence-gallery');
    for(const img of rows('validation_evidence').filter(r=>r.validation_id===v.id)) {
      const url='/api/evidence?'+new URLSearchParams({id:img.id,database:getData().schema.database}),figure=node('figure'),a=link('',url),image=node('img');image.src=url;image.alt=img.label;image.loading='lazy';a.append(image);figure.append(a,node('figcaption',img.label));gallery.append(figure);
    }
    return gallery;
  }
  function pageReference(label,url,className='review-page') {
    const p=node('p',undefined,className);p.append(node('span',label+' '),link(url,url));return p;
  }
  function checkedPageLabel(url) {
    let host;try{host=new URL(url).hostname;}catch{return 'Checked page';}
    return host==='localhost'||host.endsWith('.localhost')||host.startsWith('127.')||host==='[::1]'||host==='0.0.0.0'?'Checked page · local preview':'Checked page';
  }
  function evidenceRecord(v,current=true) {
    const box=node('div',undefined,'validation-record'),notes=node('div',undefined,'validation-notes'),gallery=evidence(v);
    if(v.recommendation){const recommendation=node('p',undefined,'agent-recommendation');recommendation.append(node('span',`${v.reporter} recommends `),node('strong',{accept:'Accept',needs_work:'Needs work',revert:'Revert'}[v.recommendation]));notes.append(recommendation);}
    notes.append(node('p',v.summary,'quote'));
    const provenance=node('div',undefined,'check-provenance'),commit=link(`Commit ${v.revision.slice(0,7)} ↗`,v.pr_url.replace(/\/pull\/\d+$/,`/commit/${v.revision}`));commit.title=v.revision;
    provenance.append(node('span',`${v.reporter} · ${date(v.created_at)}`),link(`PR #${v.pr_url.split('/').pop()}`,v.pr_url),commit);notes.append(provenance);
    if(v.target_url)notes.append(pageReference(checkedPageLabel(v.target_url),v.target_url));
    if(current&&!fresh(v))notes.append(node('p','This evidence needs a fresh check against the current PR revision.','review-warning'));
    box.append(notes);
    if(gallery.children.length){box.classList.add('has-evidence');box.append(gallery);}
    else notes.append(node('p','No screenshots attached.','evidence-absence'));
    return box;
  }
  function recordForm(batch,item,v) {
    const key=`record-${batch.id}-${item.id}`,d=node('div',undefined,'record-check'),form=node('form',undefined,'validation-form');
    const actions=node('div',undefined,'check-actions'),record=button(v?'Record another check…':'Record a check…',()=>{if(drafts.size&&!drafts.has(form)){showError(Error('Save or cancel your current edits first.'));return;}form.hidden=!form.hidden;record.setAttribute('aria-expanded',String(!form.hidden));if(form.hidden)openItems.delete(key);else{openItems.add(key);summary.focus();}},(!v||!fresh(v))?'primary':'subtle-action');
    form.hidden=!openItems.has(key);form.id=key;record.setAttribute('aria-controls',key);record.setAttribute('aria-expanded',String(!form.hidden));
    actions.append(record);d.append(actions);
    const pr=node('select');pr.required=true;for(const p of prs(batch.id)){const o=node('option',`PR #${p.url.split('/').pop()}`);o.value=p.url;pr.append(o);}
    const revision=input('text');revision.required=true;revision.pattern='[0-9a-fA-F]{40}';revision.placeholder='Full commit SHA inspected';
    const fillRevision=()=>{revision.value=metadata(batch.id,pr.value).value?.head_sha||'';};fillRevision();pr.addEventListener('change',fillRevision);
    const outcome=node('select');for(const [value,label] of [['pass','Pass'],['needs_work','Needs work'],['blocked','Blocked']]){const o=node('option',label);o.value=value;outcome.append(o);}
    const target=input('url');target.placeholder='URL actually inspected (optional)';
    const summary=node('textarea',undefined,'fix-notes');summary.required=true;summary.rows=3;summary.placeholder='Briefly state what you checked and observed. Add a concern or limitation only if it matters.';
    const images=input('file');images.accept='image/png,image/jpeg,image/webp';images.multiple=true;
    const grid=node('div',undefined,'validation-grid');grid.append(field('PR',pr),field('Commit checked',revision),field('Outcome',outcome),field('Page URL checked',target));
    const save=node('button','Save check');save.type='submit';form.append(grid,field('Validation notes',summary),field('Screenshots (optional · up to 4, 5 MB each)',images),node('p','Saving records a result for your review. Accept passing evidence separately.','meta'),save,button('Cancel',()=>{drafts.delete(form);form.reset();form.hidden=true;openItems.delete(key);render(true);}));d.append(form);
    form.addEventListener('input',()=>drafts.add(form));
    form.addEventListener('submit',async e=>{e.preventDefault();busy++;save.disabled=true;try{
      const files=[...images.files];if(files.length>4||files.some(f=>f.size>5*1024*1024))throw Error('Use up to four screenshots, at most 5 MB each.');
      const encoded=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({label:file.name,content:reader.result.split(',')[1]});reader.onerror=reject;reader.readAsDataURL(file);})));
      await mutate('/api/validation',{batch_id:batch.id,finding_id:item.id,pr_url:pr.value,revision:revision.value,outcome:outcome.value,summary:summary.value,target_url:target.value,images:encoded});drafts.delete(form);openItems.delete(`record-${batch.id}-${item.id}`);await refresh();
    }catch(err){showError(err);}finally{busy--;save.disabled=false;}});
    return d;
  }
  async function outstanding(batch) {
    if(drafts.size){showError(Error('Save or cancel your current edits before copying the outstanding list.'));return;}
    const dialog=node('dialog',undefined,'validation-dialog'),heading=node('div',undefined,'section-heading');
    heading.append(node('h2',`Batch #${batch.id} · Not accepted`),button('×',()=>dialog.close()));
    dialog.append(heading,node('p','Every finding still awaiting acceptance, including your change and revert requests. Copy this list for a manual handoff.','section-note'));
    const area=node('textarea',undefined,'agent-prompt');area.readOnly=true;area.rows=16;area.setAttribute('aria-label','Outstanding findings');
    const status=node('p','','meta'),copy=button('Copy outstanding list',async()=>{try{await navigator.clipboard.writeText(area.value);status.textContent='Copied · No agent has been started';}catch(e){status.textContent='Select and copy the list above.';}},'primary');copy.disabled=true;
    dialog.append(area,copy,status);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
    try{area.value=(await mutate('/api/outstanding-findings',{batch_id:batch.id})).text;copy.disabled=false;}catch(e){status.textContent=e.message;}
  }
  function nextFinding(batch,item) {
    const visible=visibleItems(batch),index=visible.findIndex(i=>i.id===item.id);
    return visible[index+1];
  }
  function focusFinding(batch,item) {
    for(const el of document.querySelectorAll(`[data-review-batch="${batch.id}"]`)) {
      if(el.dataset.findingId===item?.id){el.open=true;el.querySelector('summary').focus();el.scrollIntoView({block:'nearest'});return;}
    }
    document.querySelector(`[data-queue="${batch.id}"] summary`)?.focus();
  }
  function openNext(batch,item) {
    const next=nextFinding(batch,item);
    openItems.delete(`finding-${batch.id}-${item.id}`);
    if(next)openItems.add(`finding-${batch.id}-${next.id}`);
    return next;
  }
  async function decide(batch,item,action,notes,b,form) {
    if([...drafts].some(draft=>draft!==form)){showError(Error('Save or cancel your current check before making a decision.'));return;}
    const state=queueRow(batch.id,item.id),label=b.textContent;busy++;b.disabled=true;b.setAttribute('aria-busy','true');if(action==='accept')b.textContent='Checking PR…';
    try {
      await mutate('/api/finding-decision',{batch_id:batch.id,finding_id:item.id,validation_id:state.validation_id,reviewed_at:state.reviewed_at,action,notes});
      if(form)drafts.delete(form);
      const next=openNext(batch,item);
      announcement=`${item.id}: ${action==='accept'?'Accepted':action==='revert'?'Revert requested':action==='needs_work'?'Needs work':'Decision cleared'}.${next?'':' End of this queue.'}`;
      await refresh();focusFinding(batch,next);
    }catch(e){showError(e);}finally{busy--;if(b.isConnected){b.disabled=false;b.removeAttribute('aria-busy');b.textContent=label;}}
  }
  function decisionControls(batch,item,state) {
    const box=node('section',undefined,'review-decision'),actions=node('div',undefined,'decision-actions');
    box.append(node('h4','2. Your decision'));
    if(state.can_accept)box.append(node('p','Review the check and any screenshots above, then decide.','meta'));
    if(state.review_action){box.append(node('p',state.review_notes,'operator-instruction'));}
    const accept=button('Accept & next',b=>decide(batch,item,'accept','',b));accept.disabled=!state.can_accept;
    accept.title=state.can_accept?'Accept the latest passing evidence':'Requires passing evidence for the current PR commit';
    const reason=node('div');
    function request(action) {
      if(drafts.size){showError(Error('Save or cancel your current edits first.'));return;}
      const check=rows('finding_validations').find(v=>v.id===state.validation_id);
      const agentFeedback=check?.reporter==='Agent'&&(check.outcome==='needs_work'||check.recommendation==='needs_work')?check.summary:'';
      const form=node('form',undefined,'decision-form'),notes=node('textarea',undefined,'fix-notes');notes.rows=3;notes.required=true;notes.value=state.review_notes||(action==='needs_work'?agentFeedback:'');
      const label=action==='revert'?'What should be reverted, and why?':'What needs to change?';notes.placeholder=label;
      form.append(field(label,notes));
      if(action==='revert')form.append(node('p','Records a request to undo this change. It does not run Git or modify the PR.','meta'));
      const save=node('button',action==='revert'?'Save revert request & next':'Save needs work & next','primary');save.type='submit';
      form.append(save,button('Cancel',()=>{drafts.delete(form);reason.replaceChildren();actions.hidden=false;}));reason.replaceChildren(form);actions.hidden=true;drafts.add(form);notes.focus();
      form.addEventListener('submit',e=>{e.preventDefault();decide(batch,item,action,notes.value,save,form);});
    }
    const reReview=button('Re-Review…',()=>prompt(batch,item));reReview.disabled=!prs(batch.id).length;reReview.title='Prepare a new review for this finding only';
    actions.append(accept,button('Needs work…',()=>request('needs_work')),button('Revert…',()=>request('revert')),reReview);
    if(state.state==='accepted'||state.review_action)actions.append(button('Clear decision',b=>decide(batch,item,'clear','',b),'subtle-action'));
    box.append(actions,reason);
    if(!state.can_accept)box.append(node('p','Acceptance needs a passing check at the current PR revision. You can still request changes now.','meta'));
    return box;
  }
  function finding(batch,item,done) {
    const v=latest(batch.id,item.id),state=queueRow(batch.id,item.id),d=details('',`finding-${batch.id}-${item.id}`),summary=d.firstChild;
    d.dataset.reviewBatch=batch.id;d.dataset.findingId=item.id;
    summary.append(node('span',item.id,'review-id'),node('span',item.title,'review-title'),node('span',state.label,'review-badge '+state.state));d.className='review-finding';
    summary.addEventListener('click',e=>{if([...drafts].some(form=>!d.contains(form))){e.preventDefault();showError(Error('Save or cancel your current edits before opening another finding.'));}});
    d.addEventListener('toggle',()=>{if(d.open&&d.parentElement){for(const other of d.parentElement.querySelectorAll('.review-finding'))if(other!==d)other.open=false;}});
    const body=node('div',undefined,'review-finding-body');
    body.append(node('p',item.path+(item.line?':'+item.line:''),'path'));
    if(item.page_url)body.append(pageReference('Source page',item.page_url,'review-page source-page'));
    const context=node('div',undefined,'review-context'),problem=item.reason||item.issue;
    if(problem){const originalProblem=node('div',undefined,'expected-change');originalProblem.append(node('h4','Original problem'),node('p',problem,'quote'));context.append(originalProblem);}
    const expected=node('div',undefined,'expected-change');expected.append(node('h4','Expected change'),node('p',item.fix_notes||item.suggested_correction||item.title,'quote'));context.append(expected);body.append(context);
    const check=node('section',undefined,'check-step'),evidenceHeading=node('div',undefined,'evidence-heading');
    evidenceHeading.append(node('h4',v?'1. Review the check':'1. Check the change'));
    if(v)evidenceHeading.append(node('span',v.outcome==='pass'?'Pass':v.outcome==='needs_work'?'Needs work':'Blocked','review-badge'));
    check.append(evidenceHeading);
    if(v)check.append(evidenceRecord(v));
    else check.append(node('p','Record your check, or use Validation prompt above to hand off the whole batch.','evidence-empty'));
    body.append(check);
    const needsCheck=!v||!fresh(v);
    if(!done&&needsCheck)body.append(recordForm(batch,item,v));
    if(!done)body.append(decisionControls(batch,item,state));
    if(!done&&!needsCheck)body.append(recordForm(batch,item,v));
    const original=details('Original content',`context-${batch.id}-${item.id}`);original.className='review-reference';original.append(node('pre',item.verbatim_content||item.issue,'quote'));body.append(original);
    if(v){const history=rows('finding_validations').filter(r=>r.batch_id===batch.id&&r.finding_id===item.id&&r.id!==v.id).sort((a,b)=>b.id-a.id);if(history.length){const h=details(`${history.length} earlier ${history.length===1?'check':'checks'}`,`history-${batch.id}-${item.id}`);for(const old of history)h.append(node('p',old.outcome.replaceAll('_',' '),'review-badge'),evidenceRecord(old,false));body.append(h);}}
    const next=nextFinding(batch,item),skip=button(next?'Next finding →':'End of queue',()=>{if(drafts.size){showError(Error('Save or cancel your current edits first.'));return;}openNext(batch,item);render(true);focusFinding(batch,next);},'subtle-action');skip.disabled=!next;body.append(skip);
    d.append(body);return d;
  }
  function checklist(batch,done) {
    const items=itemsFor(batch),remaining=items.filter(i=>!accepted(batch.id,i.id)).length;
    const list=details(`Review findings · ${remaining} not accepted`,`checklist-${batch.id}`);list.className='review-checklist';list.dataset.queue=batch.id;
    list.firstChild.setAttribute('aria-label',`Review findings · ${remaining} not accepted`);list.firstChild.replaceChildren(node('span','Review findings'),node('span',`${remaining} not accepted`,'review-remaining'));
    const controls=node('div',undefined,'queue-toolbar'),select=node('select'),filter=filterFor(batch.id);
    for(const [value,label] of [['remaining','Not accepted'],['all','All findings'],['ready','Ready to review'],['needs_work','Needs work'],['revert','Revert requested'],['unchecked','Not checked'],['stale','Recheck revision'],['failed','Check failed'],['blocked','Check blocked'],['accepted','Accepted']]) {
      const count=items.filter(i=>value==='all'||(value==='remaining'?!accepted(batch.id,i.id):queueRow(batch.id,i.id).state===value)).length;
      const option=node('option',`${label} (${count})`);option.value=value;select.append(option);
    }
    select.value=filter.state;
    const text=input('search',filter.text);text.placeholder='ID, title, file, or instructions';
    controls.append(field('Show',select),field('Filter findings',text),button(`Outstanding (${remaining})…`,()=>outstanding(batch)));
    const rowsBox=node('div',undefined,'queue-rows');
    const repaint=()=>{rowsBox.replaceChildren(...visibleItems(batch).map(item=>finding(batch,item,done)));if(!rowsBox.children.length)rowsBox.append(node('p','No findings match this view.','empty-panel'));};
    const update=()=>{if(drafts.size){showError(Error('Save or cancel your current edits before filtering.'));select.value=filterFor(batch.id).state;text.value=filterFor(batch.id).text;return;}filters.set(batch.id,{state:select.value,text:text.value});repaint();};
    select.addEventListener('change',update);text.addEventListener('input',update);
    list.firstChild.addEventListener('click',e=>{if(!list.open&&drafts.size){e.preventDefault();showError(Error('Save or cancel your current edits before opening another batch.'));}});
    list.addEventListener('toggle',()=>{if(list.open&&list.isConnected&&!list.querySelector('.review-finding[open]')){const first=rowsBox.querySelector('.review-finding');if(first)first.open=true;}});
    list.append(controls,rowsBox);repaint();return list;
  }
  function render(force=false) {
    const data=getData();if(!data.schema)return;
    const batches=data.batches.filter(b=>b.status!=='draft').sort((a,b)=>b.id-a.id),active=batches.filter(b=>!completed(b.id)),finished=batches.filter(b=>completed(b.id));
    $('progress-tab-count').textContent=`(${active.length})`;$('progress-title').textContent='In progress';
    const next=JSON.stringify([data.schema.database,batches,data.findings,data.batch_pull_requests,data.batch_findings,...['pull_request_details','finding_validations','validation_evidence','batch_completions','review_queue'].map(rows)]);
    if(drafts.size||(!force&&(signature===next||document.activeElement?.closest('#progress-batches'))))return;
    if(source!==data.schema.database){source=data.schema.database;openItems.clear();filters.clear();announcement='';}
    signature=next;const container=$('progress-batches');container.replaceChildren();
    const feedback=node('p',announcement,'review-feedback');feedback.setAttribute('role','status');container.append(feedback);
    const history=details(`Completed (${finished.length})`,'completed-batches');history.className='completed-batches';
    for(const batch of [...active,...finished]) {
      const done=completed(batch.id),items=itemsFor(batch),count=items.filter(i=>accepted(batch.id,i.id)).length;
      const card=node('article',undefined,'progress-batch');card.dataset.batchId=batch.id;
      const overview=node('div',undefined,'batch-overview'),summary=node('div',undefined,'batch-summary'),status=node('div',undefined,'batch-status');
      summary.append(node('h3',`Batch #${batch.id}`),node('p',`${items.length} ${items.length===1?'finding':'findings'} · ${batch.base_branch||'Base unset'}${done?' · Completed '+date(done.completed_at):''}`,'batch-meta'));
      for(const pr of prs(batch.id))summary.append(prCard(batch,pr));
      if(!prs(batch.id).length)summary.append(node('p','No PR linked yet. Add its URL when the work is ready.','section-note'));
      status.append(node('p',done?'Completed':`${count}/${items.length} accepted`,'batch-progress-label'));
      const progress=node('progress');progress.max=items.length||1;progress.value=count;progress.setAttribute('aria-label',`Batch #${batch.id}: ${count} of ${items.length} findings accepted`);status.append(progress);
      const actions=node('div',undefined,'review-actions');
      if(!done) {
        const complete=button('Complete batch',b=>act('/api/complete-batch',{batch_id:batch.id},b));complete.disabled=!items.length||count!==items.length;complete.title='Requires accepted evidence for every finding at the current PR commit';actions.append(complete);
      }else actions.append(button('Reopen batch',b=>act('/api/reopen-batch',{batch_id:batch.id},b)));
      status.append(actions);overview.append(summary,status);card.append(overview,checklist(batch,done));
      const setup=details('Batch tools & worktree',`setup-${batch.id}`),tools=node('div',undefined,'pr-tools-actions'),dl=node('dl',undefined,'progress-setup');
      if(!done){const prepare=button('Validation prompt…',()=>prompt(batch),'subtle-action');prepare.disabled=!prs(batch.id).length;tools.append(prepare);}
      if(prs(batch.id).length)tools.append(button('Refresh PRs',b=>act('/api/refresh-prs',{batch_id:batch.id},b)));
      if(!done)for(const pr of prs(batch.id)) {
        const linked=node('div',undefined,'linked-pr-tool'),remove=button('Unlink',b=>{if(confirm('Unlink this PR from the batch? The PR stays on GitHub.'))act('/api/remove-pr',{batch_id:batch.id,url:pr.url},b);},'subtle-action');
        remove.setAttribute('aria-label',`Unlink PR #${pr.url.split('/').pop()}`);remove.title='Remove this PR link from the batch';linked.append(link(`PR #${pr.url.split('/').pop()}`,pr.url),remove);tools.append(linked);
      }
      setup.className='batch-tools';setup.append(tools);if(!done)setup.append(addPr(batch));
      for(const [label,value] of [['Branch',batch.branch],['Worktree',batch.worktree],['Base',batch.base_branch]]){const dd=node('dd');dd.append(node('code',value||'—'));dl.append(node('dt',label),dd);}setup.append(dl,node('p','Agent startup and PR merges are manual. Complete batch records your acceptance in Auditorium.','meta'));card.append(setup);
      (done?history:container).append(card);
    }
    if(!active.length)container.append(node('p','No batches are in progress. Submitted batches appear here.','empty-panel'));
    if(finished.length)container.append(history);
  }
  return {render,completed,hasEdits:()=>drafts.size>0||busy>0};
};
