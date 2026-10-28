"""Four strategies over one frozen corpus. No evaluation references enter here."""
from time import perf_counter
import re
from doc_understanding.document_rag import repository
from doc_understanding.document_rag.index import evidence_pack, cells_for_table, lexical_score, display_path
from doc_understanding.document_rag.answer import render_answer
from doc_understanding.document_rag.telemetry import span, candidates_summary
from . import store
from .projection import parse_question, path_integrity
from .ranking import rrf, column_matches, row_exactness


class Retriever:
    def __init__(self, manifest, projections, embeddings, documents, *, method, allow_unverified_row=False,
                 scope_institution=True, tree_for_keyword=False):
        if method not in ('A', 'B', 'C', 'D'):
            raise ValueError('UNKNOWN_RETRIEVAL_METHOD')
        self.manifest, self.embeddings, self.documents, self.method = manifest, embeddings, documents, method
        self.projections = projections
        self.tables = {p.table_id: p for p in projections if p.kind == 'table'}
        self.rows = {}
        for p in projections:
            if p.kind == 'row':
                self.rows.setdefault(p.table_id, []).append(p)
        self.institutions = sorted({p.institution for p in projections if p.institution})
        self.last = {}
        self.failure = []
        self.allow_unverified_row = allow_unverified_row
        self.scope_institution = scope_institution
        self.tree_for_keyword = tree_for_keyword
        self.use_tree = method in ('C','D') or (method=='A' and tree_for_keyword)

    def reset(self):
        self.last = dict(method=self.method, keyword_candidates=[], vector_candidates=[], row_candidates=[], timings={})
        self.failure = []
        self.chosen_row = None

    @staticmethod
    def table_hit(hit):
        p = hit['payload']
        return dict(run_id=hit['run_id'], table_id=hit['table_id'], document_id=hit['document_id'], score=hit['score'],
                    payload=dict(kind='table', table_id=hit['table_id'], page=p['page'],
                        entry_id=p['projection_id'], content_id=p['source_ids'][0], cell_ids=[],
                        context_content_ids=p['metadata'].get('context_content_ids', [])))

    def retrieve(self, question, run_id=None, limit=5):
        self.reset()
        self.conditions = parse_question(question, self.institutions)
        scoped = [p.run_id for p in self.tables.values() if not self.scope_institution or not self.conditions['institution'] or
                  p.institution == self.conditions['institution']]
        scoped = list(dict.fromkeys(scoped))
        if run_id:
            scoped = [rid for rid in scoped if rid == run_id]
        keyword, vector = [], []
        if self.method in ('A', 'D'):
            started = perf_counter()
            with span('experiment.keyword_retrieval', dict(question=question, method=self.method,
                      search_request=self.conditions)) as record:
                hits = repository.search(question, limit=30, run_ids=scoped)
                seen = set()
                for hit in hits:
                    tid = hit['payload'].get('table_id')
                    if tid in self.tables and tid not in seen:
                        seen.add(tid)
                        keyword.append({**hit, 'table_id': tid, 'document_id': self.tables[tid].document_id})
                record['output'] = dict(candidates=candidates_summary(keyword), score_method='existing_keyword_sql_no_score_changes')
            self.last['timings']['keyword_seconds'] = perf_counter()-started
            self.last['keyword_candidates'] = candidates_summary(keyword)
        if self.method in ('B', 'C', 'D'):
            started = perf_counter()
            with span('experiment.vector_retrieval', dict(question=question, method=self.method,
                      search_request=self.conditions, score_method='1 - pgvector cosine_distance, exact scan')) as record:
                query_vector = self.embeddings.embed([question])[0]
                hits = store.search(self.manifest['corpus_id'], query_vector,
                                    institution=self.conditions['institution'] if self.scope_institution else None, limit=10)
                vector = [self.table_hit(h) for h in hits if h['run_id'] in scoped]
                record['output'] = dict(candidates=candidates_summary(vector))
            self.last['timings']['vector_seconds'] = perf_counter()-started
            self.last['vector_candidates'] = candidates_summary(vector)
        ranked = keyword if self.method == 'A' else rrf(keyword, vector) if self.method == 'D' else vector
        self.last['table_ranking_before_tree'] = candidates_summary(ranked[:5])
        if self.use_tree and ranked:
            ranked = self.rerank_by_row_path(ranked[:5], question)
        with span('experiment.final_table_selection', dict(method=self.method, conditions=self.conditions,
                  score_method='RRF(k=60)' if self.method == 'D' else self.method)) as record:
            ranked = ranked[:5]
            record['output'] = dict(candidates=candidates_summary(ranked),
                                   selected=ranked[0]['table_id'] if ranked else None)
        self.last['conditions'] = self.conditions
        self.last['table_ranking'] = candidates_summary(ranked)
        return ranked

    def rerank_by_row_path(self, ranked, question):
        """C/D use row paths across the SAME top-five tables, not a gold table filter."""
        ids = [h['table_id'] for h in ranked]
        row_query = self.conditions['row_query']
        with span('experiment.row_path_candidate_rerank', dict(query=row_query,
                  method=self.method, candidates=candidates_summary(ranked))) as record:
            if self.method == 'A' and self.tree_for_keyword:
                dense = []  # Explicit path lookup requires no embedding or vector database.
            else:
                vectors = self.embeddings.embed([row_query])[0]
                dense = store.search(self.manifest['corpus_id'], vectors, kind='row', table_ids=ids, limit=30)
            self.last['row_candidates'] = [dict(entry_id=h['projection_id'], table_id=h['table_id'],
                score=h['score'], row_path=h['payload']['metadata']['row_path'],
                row_node_ids=h['payload']['row_node_ids']) for h in dense]
            explicit = [(row_exactness(p.metadata['row_path'], row_query), p) for tid in ids for p in self.rows.get(tid, [])
                        if p.metadata['eligible']]
            best = max((score for score, p in explicit), default=0)
            exact = [p for score, p in explicit if best and score == best]
            if len(exact) == 1:
                p = exact[0]
                self.chosen_row = dict(table_id=p.table_id, row_node_ids=p.row_node_ids,
                                       relation='explicit_label_path_candidate')
            elif not exact:
                eligible = [h for h in dense if h['payload']['metadata']['eligible']]
                if eligible and eligible[0]['score'] >= .35 and (
                    len(eligible)==1 or eligible[0]['score']-eligible[1]['score'] >= .04):
                    proposed = dict(table_id=eligible[0]['table_id'],
                        row_node_ids=eligible[0]['payload']['row_node_ids'], relation='vector_row_hypothesis_not_validated')
                    self.last['unverified_row_proposal'] = proposed
                    if self.allow_unverified_row:
                        self.chosen_row = proposed
            if self.chosen_row:
                tid = self.chosen_row['table_id']
                ranked = sorted(ranked, key=lambda h: h['table_id'] != tid)
            record['output'] = dict(candidates=self.last['row_candidates'], selected=self.chosen_row,
                                   reason='top-five row-path match; original table scores retained')
        return ranked

    def fail(self, reason):
        self.failure.append(reason)
        return []

    def resolve(self, hits, question):
        started = perf_counter()
        with span('experiment.tree_or_flat_lookup', dict(question=question, method=self.method,
                  conditions=self.conditions)) as record:
            evidence = self._resolve(hits, question)
            self.last['timings']['lookup_seconds'] = perf_counter()-started
            self.last['selected'] = evidence
            self.last['reasons'] = self.failure
            record['output'] = dict(evidence=evidence, reasons=self.failure,
                                   candidates=self.last['row_candidates'])
        return evidence

    def _resolve(self, hits, question):
        if not hits:
            return self.fail('NO_TABLE_CANDIDATE')
        names = {self.tables[h['table_id']].institution for h in hits}
        if self.conditions['institution_ambiguous'] or (not self.conditions['institution'] and len(names) > 1):
            return self.fail('AMBIGUOUS_INSTITUTION')
        tid, rid = hits[0]['table_id'], hits[0]['run_id']
        doc = self.documents[rid]
        table = next(t for t in doc.table_structure if t.table_id == tid)
        all_cells = cells_for_table(doc, tid)
        if not any(path_integrity(table, c) for c in all_cells):
            return self.fail('UNRESOLVED_STRUCTURAL_PATHS')
        periods = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',
                      ' '.join(label or '' for c in all_cells for label in c['column_path'])))
        if not set(self.conditions['periods']).issubset(periods):
            return self.fail('REQUESTED_PERIOD_NOT_IN_COLUMN_TREE')
        if not self.use_tree:
            # Same existing evidence_pack flattening and lexical score, not a second LLM.
            pool = evidence_pack(doc, [hits[0]['payload']], question, max_cells=35)
            pool = [c for c in pool if c.get('table_id') and column_matches(c['column_path'], self.conditions)
                    and path_integrity(table, c)]
            if not pool:
                return self.fail('NO_FLAT_CELL_WITH_EXPLICIT_COLUMN')
            scores = [lexical_score(display_path(c['row_path']+c['column_path']), question) for c in pool]
            best = [c for c, score in zip(pool, scores, strict=True) if score == max(scores)]
            if len(best) != 1:
                return self.fail('AMBIGUOUS_FLAT_CELL_MATCH')
            cell = best[0]
            relation = 'existing_flat_lexical_candidate'
        else:
            row_query = self.conditions['row_query']
            if self.chosen_row and self.chosen_row['table_id'] == tid:
                row_node_ids = self.chosen_row['row_node_ids']
                relation = self.chosen_row['relation']
                dense_rows = []
            else:
                if self.method == 'A' and self.tree_for_keyword:
                    return self.fail('UNVERIFIED_ROW_SEMANTICS_CANDIDATE_ONLY')
                with span('experiment.row_path_vector_search', dict(query=row_query, table_id=tid,
                      score_method='cosine + explicit leaf/path match, not semantic approval')) as record:
                    query_vector = self.embeddings.embed([row_query])[0]
                    dense_rows = store.search(self.manifest['corpus_id'], query_vector, kind='row', table_ids=[tid], limit=10)
                    candidates = [dict(entry_id=h['projection_id'], table_id=tid, score=h['score'],
                        row_path=h['payload']['metadata']['row_path'], row_node_ids=h['payload']['row_node_ids']) for h in dense_rows]
                    self.last['row_candidates'] = candidates
                    record['output'] = dict(candidates=candidates)
            exact = [(row_exactness(p.metadata['row_path'], row_query), p) for p in self.rows.get(tid, [])]
            best_score = max((score for score, p in exact), default=0)
            exact = [p for score, p in exact if best_score and score == best_score]
            if self.chosen_row and self.chosen_row['table_id'] == tid:
                pass
            elif exact:
                if len(exact) != 1:
                    return self.fail('AMBIGUOUS_ROW_TREE_PATH')
                row_node_ids = exact[0].row_node_ids
                relation = 'explicit_label_path_candidate'
            else:
                if not self.allow_unverified_row:
                    return self.fail('UNVERIFIED_ROW_SEMANTICS_CANDIDATE_ONLY')
                if not dense_rows or dense_rows[0]['score'] < .35:
                    return self.fail('ROW_VECTOR_BELOW_FIXED_THRESHOLD')
                margin = dense_rows[0]['score']-(dense_rows[1]['score'] if len(dense_rows)>1 else 0)
                if margin < .04:
                    return self.fail('AMBIGUOUS_ROW_VECTOR_MARGIN')
                row_node_ids = dense_rows[0]['payload']['row_node_ids']
                relation = 'vector_row_hypothesis_not_validated'
            cells = [c for c in all_cells if c['row_node_ids'] == row_node_ids
                     and column_matches(c['column_path'], self.conditions)]
            if len(cells) != 1:
                return self.fail('AMBIGUOUS_OR_MISSING_COLUMN_TREE_PATH')
            cell = cells[0]
            if not path_integrity(table, cell):
                return self.fail('UNRESOLVED_SELECTED_ROW_COLUMN_LINK')
        # An unrelated modifier/translation cannot be established by substring score
        # or vector similarity alone. Keep it as a proposal, not an answered number.
        if not self.allow_unverified_row and not row_exactness(cell['row_path'], self.conditions['row_query']):
            return self.fail('UNVERIFIED_ROW_SEMANTICS_CANDIDATE_ONLY')
        return [{**cell, 'evidence_id': 'E1', 'run_id': rid, 'retrieval_relation': relation}]

    def validate(self, evidence, question):
        return dict(sufficient=bool(evidence) and not self.failure, reasons=self.failure,
                    semantic_status='unvalidated_candidate', facts_published=0)

    def deterministic_answer(self, question, evidence, output, **unused):
        for e in evidence:
            rows = repository.sql_value_probe(e['run_id'], e['table_id'], e['cell_id'])
            if not rows or any(rows[0][k] != e[k] for k in ('value_raw', 'row_node_ids', 'column_node_ids')):
                raise ValueError('ACTUAL_SQL_CELL_PATH_MISMATCH')
        selection = dict(evidence_ids=[e['evidence_id'] for e in evidence], values_raw=[e['value_raw'] for e in evidence],
                         abstained=not bool(evidence))
        return dict(question=question, answer=render_answer(selection, evidence), selection=selection,
                    reasons=self.failure, generation_mode='deterministic_retrieval_evaluation_no_llm',
                    model_calls=0, estimated_cost_usd=0, facts_published=0,
                    semantic_status='unvalidated_candidate')
