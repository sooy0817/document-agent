(function(root){
  function metrics(items){
    const attempts=items.flatMap(i=>(i.costs||[]).map(c=>({...c,id:i.id})));
    const known=attempts.filter(c=>typeof c.estimated_cost_usd==='number');
    return {attempts,knownCost:known.reduce((s,c)=>s+c.estimated_cost_usd,0),unknownCost:attempts.length-known.length,
      finished:items.filter(i=>i.result).length,
      verified:items.filter(i=>i.result?.semantic_accuracy_verified===true).length,
      comparisons:items.flatMap(i=>(i.result?.datasets||[]).map(d=>({...d,id:i.id})))};
  }
  // Readiness is descriptive, never an approval inferred from model feedback.
  function verification(item){
    const r=item?.result, rows=r?.datasets||[];
    const candidate=!!r?.candidate_modified && r.mode!=='local_refresh';
    const comparison=candidate && rows.some(d=>d.dataset?.role==='development' && d.comparison);
    const tests=!!r?.tests && r.tests.baseline===0 && r.tests.candidate===0;
    const regressions=rows.filter(d=>d.dataset?.role==='regression');
    const regression=regressions.length>0 && regressions.every(d=>d.comparison?.preservation?.passed===true);
    return {candidate,comparison,tests,regression,applied:!!r?.stable_parser_modified,
      canConfirm:false, // Bound human decision + re-scoring + promotion transaction is not connected yet.
      headline:!candidate?'아직 채점할 새 파서 후보가 없습니다.':r.stable_parser_modified?'과거 반영 결과입니다. 새 후보의 최종 승인이 아닙니다.':'후보 비교 자료를 확인할 수 있습니다. 최종 확정 연결은 준비 중입니다.'};
  }
  const value={metrics,verification};if(typeof module!=='undefined')module.exports=value;else root.RepairMetrics=value;
})(typeof window==='undefined'?globalThis:window);
