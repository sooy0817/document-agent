(function(root){
  const labels={queued:'실행 대기',running:'분석 중',completed:'분석 완료',failed:'실행 실패',cancelled:'취소됨'};
  const steps={
    candidates:{label:'규칙 경로 생성',owner:'규칙 코드',purpose:'원본 셀과 앞 페이지 헤더를 근거로 행·열 경로 후보를 만듭니다.',file:'src/doc_understanding/tools/semantic_candidates.py'},
    gate:{label:'근거 충분성 판단',owner:'규칙 코드',purpose:'경로 누락·충돌·사용자 이견이 있는 셀만 LLM 검토로 보냅니다.',file:'src/doc_understanding/validation/semantic_gate.py'},
    source:{label:'원본·근거 확인',owner:'코드 검사',purpose:'저장한 PDF·JSON 해시와 검토 범위를 확인합니다.',file:'src/doc_understanding/validation/source_checks.py'},
    roles:{label:'셀 역할 판단',owner:'설정된 검토 모델',purpose:'설명·값·제목의 역할을 분류합니다. 적용 관계는 별도로 판단합니다.',file:'src/doc_understanding/agents/workflows/table_review/graph.py'},
    relations:{label:'적용 관계 판단',owner:'설정된 검토 모델',purpose:'각 셀의 행·열 항목과 원문 근거를 제안합니다.',file:'src/doc_understanding/agents/workflows/table_review/graph.py'},
    judge:{label:'별도 검수',owner:'설정된 검토 모델',purpose:'관계의 누락·모순·근거 부족을 검토합니다. 모델 의견은 정답 승인이 아닙니다.',file:'src/doc_understanding/agents/workflows/table_review/graph.py'},
    validate:{label:'코드 검증',owner:'코드 검사',purpose:'ID·인용·누락·저장된 관계를 검사합니다. 코드를 수정하는 단계가 아닙니다.',file:'src/doc_understanding/agents/workflows/table_review/graph.py'},
    report:{label:'결과 기록',owner:'검토 큐',purpose:'결과와 비용을 저장하고 이전 실행과 연결합니다.',file:'src/doc_understanding/runtime/review_queue.py'},
    human:{label:'사용자 확인',owner:'나',purpose:'원본을 보고 의견을 남기면 별도 재검토 요청이 생성됩니다.',file:'src/doc_understanding/runtime/review_queue.py'},
    analyze:{label:'오류 분석',owner:'설정된 검토 모델',purpose:'기존 분석 모드의 원본 근거와 오류 후보를 검토합니다.',file:'src/doc_understanding/agents/workflows/legacy_review/graph.py'},
    observe:{label:'원본 선행 검토',owner:'설정된 검토 모델',purpose:'분석 의견을 보기 전에 원본을 검토합니다.',file:'src/doc_understanding/agents/workflows/evidence_review/graph.py'},
    expand:{label:'근거 보완',owner:'조회 도구',purpose:'요청된 근거를 최대 한 번 보완합니다. 기록이 있을 때만 실행으로 표시합니다.',file:'src/doc_understanding/agents/workflows/evidence_review/graph.py'},
    checks:{label:'구조·연결 검사',owner:'로컬 코드',purpose:'기존 ID·경로·연결을 검사합니다. 모델 의미 검수는 없습니다.',file:'src/doc_understanding/validation/source_checks.py'}
  };
  const stageKey={source_integrity:'source',collect_evidence:'source',structure_checks:'source',context_checks:'source',
    semantic_candidates:'candidates',semantic_gate:'gate',
    classify_roles:'roles',infer_relations:'relations',judge_relations:'judge',validate_relations_and_storage:'validate',
    report_ready:'report',source_review:'observe',analyze:'analyze',analyst_v2:'analyze',reanalysis:'analyze',
    expand_evidence:'expand',judge:'judge',judge_v2:'judge',validate_review:'validate'};
  function actionable(item){return !item.decision&&!item.confirmation&&(item.status==='failed'||item.status==='completed'&&item.result&&item.result.verdict!=='no_structural_issue_detected');}
  function latest(items){const seen=new Set();return items.filter(i=>{if(seen.has(i.item_id))return false;seen.add(i.item_id);return true;});}
  function stages(item){
    if(!item)return [];
    let keys=item.mode==='agents_roles'?['source','roles','relations','judge','validate','report','human']:
      item.mode==='agents_v2'?['source','observe','analyze','expand','judge','validate','report','human']:
      item.mode==='agents'?['source','analyze','judge','validate','report','human']:['source','checks','report','human'];
    const events=(item.history||[]).filter(e=>e.attempt===item.attempt&&e.kind==='stage');
    const gated=!!item.result?.routing_summary||events.some(e=>e.payload.stage==='semantic_gate');
    if(gated&&item.mode==='agents_roles')keys.splice(1,0,'candidates','gate');
    const map=s=>item.mode==='local'&&['structure_checks','context_checks'].includes(s)?'checks':stageKey[s];
    const visited=new Set(events.map(e=>map(e.payload.stage)));
    let current=map(item.stage);
    if(item.status==='failed'||item.status==='cancelled')current=map(events.at(-1)?.payload.stage);
    if(item.status==='completed')visited.add('report');
    return keys.map(k=>{
      let state=visited.has(k)?'visited':'pending';
      const role={roles:'classify_roles',relations:'infer_relations',judge:'judge_relations'}[k];
      if(gated&&role&&item.status==='completed'&&!visited.has(k)&&!(item.result.call_records||[]).some(r=>r.role===role))state='skipped';
      if(item.status==='running'&&current===k)state='running';
      if(item.status==='failed'&&current===k)state='failed';
      if(item.status==='cancelled'&&current===k)state='cancelled';
      if(k==='human')state=item.confirmation?'confirmed':item.decision?'decided':actionable(item)&&item.status==='completed'?'attention':'pending';
      const step={...steps[k],key:k,state};
      if(step.owner==='설정된 검토 모델')step.owner=item.model||step.owner;
      if(['judge','validate'].includes(k)&&item.mode==='agents')step.file='src/doc_understanding/agents/workflows/legacy_review/graph.py';
      if(['analyze','judge','validate'].includes(k)&&item.mode==='agents_v2')step.file='src/doc_understanding/agents/workflows/evidence_review/graph.py';
      if(gated&&['roles','relations','judge','validate'].includes(k))step.file='src/doc_understanding/agents/workflows/table_review/graph.py';
      return step;
    });
  }
  // These describe a person's observation, never a parser diagnosis or Gold label.
  const observations={
    nesting:{check:'원본에서는 안쪽 표가 어느 바깥 셀의 테두리 안에 있나요? 화면에서도 그 안에 있나요?',label:'안쪽 표가 바깥 셀 밖에 나옴',seen:'원본에서는 셀 안에 있는 표가 화면에서는 밖에 따로 보입니다.',expected:'안쪽 표를 원본의 바깥 셀 안에 연결하고 앞뒤 설명과 함께 보여주세요.'},
    duplicate:{check:'원본에 한 번 있는 내용이 화면에서는 글과 표로 각각 한 번씩 나오나요?',label:'같은 내용이 두 번 보임',seen:'같은 내용이 글 나열과 별도 표 등으로 중복 표시됩니다.',expected:'원본에 한 번 있는 내용은 화면에서도 한 번만 보여주세요.'},
    missing:{check:'원본의 어떤 문장·값·표가 화면에서 보이지 않나요?',label:'표·문장·값이 빠짐',seen:'원본에 있는 표, 문장 또는 값이 화면에서 빠져 있습니다.',expected:'원본에서 빠진 부분을 보존하고 해당 위치에 연결해주세요.'},
    order:{check:'원본에서 표 앞이나 뒤에 있던 설명이 화면에서는 다른 순서로 나오나요?',label:'설명과 표의 순서가 다름',seen:'원본과 화면에서 설명문·표의 읽기 순서가 다릅니다.',expected:'원본에서 읽는 순서대로 설명과 표를 배치해주세요.'},
    axis:{check:'이 값이나 설명은 원본에서 어느 왼쪽 항목명과 위쪽 제목에 속하나요?',label:'행·열 항목이 잘못 연결됨',seen:'값이나 설명이 원본과 다른 행·열 항목에 연결됩니다.',expected:'원본의 항목명·헤더와 연결하고 적용 범위를 확인해주세요.'},
    merge:{check:'셀의 테두리와 병합 범위가 원본과 같나요? 다른 셀이 하나로 합쳐지거나 나뉘었나요?',label:'셀 병합·분리가 다름',seen:'원본과 셀의 병합 또는 분리 범위가 다릅니다.',expected:'원본의 경계와 rowspan/colspan을 비교해주세요.'},
    header:{check:'본문이 제목으로 쓰였거나, 상위 헤더가 빠졌나요? 제목 없는 영역에 제목을 붙였나요?',label:'제목·본문 역할이 다름',seen:'헤더·행 이름·본문의 역할이 원본과 다르게 표현됩니다.',expected:'원문 근거로 셀 역할과 상하위 헤더를 확인해주세요.'},
    value:{check:'숫자, 글자, 공란, 0, 음수, 단위나 기간이 원본 그대로인가요?',label:'값·글자·단위·기간이 다름',seen:'값·문자·단위·기간 중 원본과 다른 부분이 있습니다.',expected:'원문을 보존하고 공란·0·음수 및 단위·기간을 구분해주세요.'},
    context:{check:'조건·예외·주석이 어느 항목에 적용되나요? 설명이 빠지거나 다른 항목에 붙었나요?',label:'설명·조건·주석 연결이 다름',seen:'설명·조건·예외·주석의 연결이나 적용 범위가 원본과 다릅니다.',expected:'설명과 적용 대상의 원문 근거를 함께 확인해주세요.'},
    continuation:{check:'앞뒤 페이지의 같은 표가 끊겼나요? 서로 다른 표를 이어 붙였나요?',label:'계속표·페이지 연결이 다름',seen:'페이지 사이의 표 또는 헤더 연결이 원본과 다릅니다.',expected:'앞뒤 페이지를 비교하고 이어야 할 표와 분리할 표를 구분해주세요.'},
    other:{check:'원본과 화면에서 다른 부분을 추가 설명에 적어주세요.',label:'그 밖의 차이',seen:'다른 차이가 보입니다.',expected:'아래 추가 설명을 기준으로 원본과 비교해주세요.'}
  };
  function reviewNote(item,draft){
    const chosen=(draft.issues||[]).filter(k=>observations[k]);
    const choice=draft.outcome==='correct'?'agree':draft.outcome==='uncertain'?'uncertain':'disagree';
    const lines=['[사용자 관찰 · 정답 승인 아님]',`문서: ${item.document_name} / ${item.selection.page}쪽`,
      '확인 범위: '+(draft.target_label||'오류 담기 당시 선택 표·영역')];
    if(draft.location?.trim())lines.push('원본에서 찾을 위치: '+draft.location.trim());
    if(draft.outcome==='correct')lines.push('판단: 선택한 범위에서 원본과 다른 점을 발견하지 못했습니다. 문서 전체나 모든 의미 관계를 승인하는 것은 아닙니다.');
    else if(draft.outcome==='uncertain')lines.push('판단: 원본만으로 결정하기 어렵습니다. 판단 보류로 남겨주세요.');
    else for(const key of chosen){lines.push('보이는 차이: '+observations[key].seen,'기대하는 모습: '+observations[key].expected);}
    if(draft.note?.trim())lines.push('추가 설명: '+draft.note.trim());
    lines.push('표시·부모 연결·물리 추출·의미 관계 중 실제 원인은 재검토에서 구분해주세요.');
    return {choice,note:lines.join('\n'),source_ids:draft.target_id?[draft.target_id]:[]};
  }
  function opinionRun(item,items){
    const run=item.decision?items.find(v=>v.id===item.decision.child_id):item.parent_id?item:null;
    const opinion=run?.submitted_opinion||item.decision||null;
    return run&&opinion?{run,opinion}:null;
  }
  function listItems(items,filter,alerts){
    if(filter==='all')return items;
    const current=latest(items),ids=new Set(alerts.map(a=>a.request_id));
    if(filter==='confirmed')return current.filter(i=>i.confirmation);
    if(filter==='attention')return current.filter(i=>ids.has(i.id));
    if(filter==='active')return current.filter(i=>['running','queued'].includes(i.status));
    return current.filter(i=>!i.confirmation);
  }
  function reviewComparison(item,items){
    const byId=new Map(items.map(i=>[i.id,i])),seen=new Set([item.id]);
    let id=item.parent_id,baseline=null,skipped=0;
    while(id&&!seen.has(id)){
      seen.add(id);const prior=byId.get(id);if(!prior)break;
      if(prior.status==='completed'&&prior.result){baseline=prior;break;}
      skipped++;id=prior.parent_id;
    }
    const sameSource=!!baseline&&!!item.source_pdf_sha256&&item.source_pdf_sha256===baseline.source_pdf_sha256;
    const sameJSON=!!baseline&&!!item.canonical_sha256&&item.canonical_sha256===baseline.canonical_sha256;
    const comparable=!!(baseline&&sameSource&&sameJSON&&item.status==='completed'&&item.result?.interpretation&&baseline.result?.interpretation);
    const short=id=>id.split('/').slice(-2).join('/');
    // Quotes are model-supplied citations, not independently validated source labels.
    function cells(result){
      const targets=result?.interpretation?.targets||[],quotes=new Map();
      for(const t of result?.relations?.targets||[])if(t.target_quote)quotes.set(t.cell_id,t.target_quote);
      for(const t of result?.roles?.targets||[])for(const c of t.citations||[])if(c.source_id===t.cell_id&&!quotes.has(c.source_id))quotes.set(c.source_id,c.quote);
      const axis=v=>({ids:v?.header_ids||[],status:v?.status||'unknown',reason:v?.reason||'',
        text:(v?.header_ids||[]).map(id=>(v.citations||[]).find(c=>c.source_id===id)?.quote||quotes.get(id)||short(id)+' (명칭 근거 없음)').join(' → ')||({no_header:'제목 없음 후보',unknown:'미확정'})[v?.status]||'연결 없음'});
      return new Map(targets.map(t=>[t.cell_id,{quote:quotes.get(t.cell_id)||short(t.cell_id),
        role:result?.roles?.targets?.find(v=>v.cell_id===t.cell_id)?.role||t.role||'unknown',
        relationRole:t.role||'unknown',row:axis(t.row_axis),column:axis(t.column_axis),context:t.context_ids||[]} ]));
    }
    const a=cells(baseline?.result),b=cells(item.result);
    const signature=v=>v?JSON.stringify([v.role,v.relationRole,v.row.ids,v.row.status,v.column.ids,v.column.status,[...v.context].sort()]):null;
    const rows=[...new Set([...a.keys(),...b.keys()])].map(id=>({id,quote:b.get(id)?.quote||a.get(id)?.quote,before:a.get(id)||null,after:b.get(id)||null,
      changed:comparable&&signature(a.get(id))!==signature(b.get(id))}));
    const oldErrors=new Set(baseline?.result?.validation_errors||[]),newErrors=new Set(item.result?.validation_errors||[]);
    return {baseline,skipped,comparable,persistedChanged:baseline&&item.canonical_sha256&&baseline.canonical_sha256?!sameJSON:null,
      rows,changed:rows.filter(r=>r.changed),resolved:comparable?[...oldErrors].filter(e=>!newErrors.has(e)):[],
      added:comparable?[...newErrors].filter(e=>!oldErrors.has(e)):[],remaining:[...newErrors]};
  }
  const api={labels,steps,stageKey,actionable,latest,stages,observations,reviewNote,opinionRun,listItems,reviewComparison};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MonitorModel=api;
})(globalThis);
