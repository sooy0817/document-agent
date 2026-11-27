"""Verify frozen source semantics and byte-exact KB artifacts after an authorized move."""
# 역할: 2026-11-09에 허용된 파일 이동·import 경로·주석 변경만 허용한다.
# 기존 manifest와 기준 ZIP은 갱신하지 않으며 알고리즘·상수·모델·KB 결과 변경은 실패시킨다.
import ast
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path


# 사용자 승인 범위의 고정 이동표. 새 이동은 검토 없이 자동으로 허용하지 않는다.
RELOCATIONS = {'doc_understanding.table_understanding.boundary_reconstructor': 'doc_understanding.parser.physical.boundary_reconstructor',
 'doc_understanding.table_understanding.diagram_gate': 'doc_understanding.parser.physical.diagram_gate',
 'doc_understanding.table_understanding.geometry': 'doc_understanding.parser.physical.geometry',
 'doc_understanding.table_understanding.intersection_analyzer': 'doc_understanding.parser.physical.intersection_analyzer',
 'doc_understanding.table_understanding.line_detector': 'doc_understanding.parser.physical.line_detector',
 'doc_understanding.table_understanding.nested_regions': 'doc_understanding.parser.physical.nested_regions',
 'doc_understanding.table_understanding.partial_representation': 'doc_understanding.parser.physical.partial_representation',
 'doc_understanding.table_understanding.physical_grid': 'doc_understanding.parser.physical.physical_grid',
 'doc_understanding.table_understanding.physical_models': 'doc_understanding.parser.physical.physical_models',
 'doc_understanding.table_understanding.physical_validator': 'doc_understanding.parser.physical.physical_validator',
 'doc_understanding.table_understanding.region_assessor': 'doc_understanding.parser.physical.region_assessor',
 'doc_understanding.table_understanding.region_detector': 'doc_understanding.parser.physical.region_detector',
 'doc_understanding.table_understanding.row_alignment': 'doc_understanding.parser.physical.row_alignment',
 'doc_understanding.table_understanding.span_resolver': 'doc_understanding.parser.physical.span_resolver',
 'doc_understanding.table_understanding.table_identity': 'doc_understanding.parser.physical.table_identity',
 'doc_understanding.table_understanding.semantic': 'doc_understanding.parser.structure.analyzer',
 'doc_understanding.table_understanding.semantic_models': 'doc_understanding.parser.structure.models',
 'doc_understanding.table_understanding.header_evidence': 'doc_understanding.parser.structure.header_evidence',
 'doc_understanding.table_understanding.repeated_panels': 'doc_understanding.parser.structure.repeated_panels',
 'doc_understanding.table_understanding.logical_tables': 'doc_understanding.parser.relations.logical_tables',
 'doc_understanding.table_understanding.continuation_graph': 'doc_understanding.parser.relations.continuation_graph',
 'doc_understanding.table_understanding.continuation_provider': 'doc_understanding.parser.relations.continuation_provider',
 'doc_understanding.table_understanding.axis_generation': 'doc_understanding.agents.axis_review.axis_generation',
 'doc_understanding.table_understanding.axis_graph': 'doc_understanding.agents.axis_review.axis_graph',
 'doc_understanding.table_understanding.axis_judge': 'doc_understanding.agents.axis_review.axis_judge',
 'doc_understanding.table_understanding.axis_judge_b': 'doc_understanding.agents.axis_review.axis_judge_b',
 'doc_understanding.table_understanding.axis_provider': 'doc_understanding.agents.axis_review.axis_provider',
 'doc_understanding.table_understanding.axis_resolver': 'doc_understanding.agents.axis_review.axis_resolver',
 'doc_understanding.table_understanding.axis_validation': 'doc_understanding.agents.axis_review.axis_validation',
 'doc_understanding.table_understanding.period_context': 'doc_understanding.parser.context.period_context',
 'doc_understanding.table_understanding.period_coverage': 'doc_understanding.parser.context.period_coverage',
 'doc_understanding.table_understanding.period_decisions': 'doc_understanding.parser.context.period_decisions',
 'doc_understanding.table_understanding.period_expansion': 'doc_understanding.parser.context.period_expansion',
 'doc_understanding.document_assembly.__init__': 'doc_understanding.parser.assembly.__init__',
 'doc_understanding.document_assembly.document': 'doc_understanding.parser.assembly.document',
 'doc_understanding.extraction.__init__': 'doc_understanding.parser.extraction.__init__',
 'doc_understanding.extraction.physical_evidence_adapter': 'doc_understanding.parser.extraction.physical_evidence_adapter',
 'doc_understanding.extraction.pymupdf_adapter': 'doc_understanding.parser.extraction.pymupdf_adapter',
 'doc_understanding.orchestrator.__init__': 'doc_understanding.parser.orchestration.__init__',
 'doc_understanding.orchestrator.workflow': 'doc_understanding.parser.orchestration.workflow',
 'doc_understanding.table_understanding.__init__': 'doc_understanding.parser.__init__',
 'doc_understanding.table_understanding.context.__init__': 'doc_understanding.parser.context.__init__',
 'doc_understanding.table_understanding.contracts': 'doc_understanding.parser.contracts',
 'doc_understanding.table_understanding.physical.__init__': 'doc_understanding.parser.physical.__init__',
 'doc_understanding.table_understanding.relations.__init__': 'doc_understanding.parser.relations.__init__',
 'doc_understanding.table_understanding.review.__init__': 'doc_understanding.agents.axis_review.__init__',
 'doc_understanding.table_understanding.structure.__init__': 'doc_understanding.parser.structure.__init__',
 'doc_understanding.table_understanding.structure.trees': 'doc_understanding.parser.structure.trees',
 'doc_understanding.table_understanding.structure.validation': 'doc_understanding.parser.structure.validation',
 'doc_understanding.agent_contracts.__init__': 'doc_understanding.agents.contracts.__init__',
 'doc_understanding.agent_contracts.models': 'doc_understanding.agents.contracts.models',
 'doc_understanding.agent_contracts.validation': 'doc_understanding.agents.contracts.validation',
 'doc_understanding.agents.__init__': 'doc_understanding.agents.roles.__init__',
 'doc_understanding.agents.analyst': 'doc_understanding.agents.roles.analyst',
 'doc_understanding.agents.axis_interpreter': 'doc_understanding.agents.roles.axis_interpreter',
 'doc_understanding.agents.axis_judge': 'doc_understanding.agents.roles.axis_judge',
 'doc_understanding.agents.contracts': 'doc_understanding.agents.roles.contracts',
 'doc_understanding.agents.diagnostic_analyst': 'doc_understanding.agents.roles.diagnostic_analyst',
 'doc_understanding.agents.diagnostic_judge': 'doc_understanding.agents.roles.diagnostic_judge',
 'doc_understanding.agents.judge': 'doc_understanding.agents.roles.judge',
 'doc_understanding.agents.policy': 'doc_understanding.agents.roles.policy',
 'doc_understanding.agents.relation_analyst': 'doc_understanding.agents.roles.relation_analyst',
 'doc_understanding.agents.relation_judge': 'doc_understanding.agents.roles.relation_judge',
 'doc_understanding.agents.role': 'doc_understanding.agents.roles.role',
 'doc_understanding.agents.role_classifier': 'doc_understanding.agents.roles.role_classifier',
 'doc_understanding.agents.source_reviewer': 'doc_understanding.agents.roles.source_reviewer',
 'doc_understanding.semantic_resolver.__init__': 'doc_understanding.agents.semantic_resolver.__init__',
 'doc_understanding.semantic_resolver.artifacts': 'doc_understanding.agents.semantic_resolver.artifacts',
 'doc_understanding.semantic_resolver.candidates': 'doc_understanding.agents.semantic_resolver.candidates',
 'doc_understanding.semantic_resolver.context': 'doc_understanding.agents.semantic_resolver.context',
 'doc_understanding.semantic_resolver.engine': 'doc_understanding.agents.semantic_resolver.engine',
 'doc_understanding.semantic_resolver.features': 'doc_understanding.agents.semantic_resolver.features',
 'doc_understanding.semantic_resolver.group_context': 'doc_understanding.agents.semantic_resolver.group_context',
 'doc_understanding.semantic_resolver.models': 'doc_understanding.agents.semantic_resolver.models',
 'doc_understanding.semantic_resolver.path_repair': 'doc_understanding.agents.semantic_resolver.path_repair',
 'doc_understanding.semantic_resolver.providers': 'doc_understanding.agents.semantic_resolver.providers',
 'doc_understanding.semantic_resolver.repair': 'doc_understanding.agents.semantic_resolver.repair',
 'doc_understanding.semantic_resolver.validator': 'doc_understanding.agents.semantic_resolver.validator',
 'doc_understanding.teams.__init__': 'doc_understanding.agents.workflows.__init__',
 'doc_understanding.teams.axis_review.__init__': 'doc_understanding.agents.workflows.axis_review.__init__',
 'doc_understanding.teams.axis_review.graph': 'doc_understanding.agents.workflows.axis_review.graph',
 'doc_understanding.teams.axis_review.profiles': 'doc_understanding.agents.workflows.axis_review.profiles',
 'doc_understanding.teams.axis_review.state': 'doc_understanding.agents.workflows.axis_review.state',
 'doc_understanding.teams.axis_review.version': 'doc_understanding.agents.workflows.axis_review.version',
 'doc_understanding.teams.campaign.__init__': 'doc_understanding.agents.workflows.campaign.__init__',
 'doc_understanding.teams.campaign.__main__': 'doc_understanding.agents.workflows.campaign.__main__',
 'doc_understanding.teams.campaign.collection': 'doc_understanding.agents.workflows.campaign.collection',
 'doc_understanding.teams.campaign.evaluation': 'doc_understanding.evaluation.parser.run_comparison',
 'doc_understanding.teams.campaign.graph': 'doc_understanding.agents.workflows.campaign.graph',
 'doc_understanding.teams.campaign.locking': 'doc_understanding.agents.workflows.campaign.locking',
 'doc_understanding.teams.campaign.pilot': 'doc_understanding.agents.workflows.campaign.pilot',
 'doc_understanding.teams.campaign.policy': 'doc_understanding.agents.workflows.campaign.policy',
 'doc_understanding.teams.campaign.public_download': 'doc_understanding.agents.workflows.campaign.public_download',
 'doc_understanding.teams.campaign.public_sources': 'doc_understanding.agents.workflows.campaign.public_sources',
 'doc_understanding.teams.campaign.review': 'doc_understanding.agents.workflows.campaign.review',
 'doc_understanding.teams.campaign.review_requests': 'doc_understanding.agents.workflows.campaign.review_requests',
 'doc_understanding.teams.campaign.service': 'doc_understanding.agents.workflows.campaign.service',
 'doc_understanding.teams.campaign.state': 'doc_understanding.agents.workflows.campaign.state',
 'doc_understanding.teams.campaign.store': 'doc_understanding.agents.workflows.campaign.store',
 'doc_understanding.teams.evidence_review.__init__': 'doc_understanding.agents.workflows.evidence_review.__init__',
 'doc_understanding.teams.evidence_review.graph': 'doc_understanding.agents.workflows.evidence_review.graph',
 'doc_understanding.teams.evidence_review.profiles': 'doc_understanding.agents.workflows.evidence_review.profiles',
 'doc_understanding.teams.evidence_review.service': 'doc_understanding.agents.workflows.evidence_review.service',
 'doc_understanding.teams.evidence_review.state': 'doc_understanding.agents.workflows.evidence_review.state',
 'doc_understanding.teams.evidence_review.version': 'doc_understanding.agents.workflows.evidence_review.version',
 'doc_understanding.teams.legacy_review.__init__': 'doc_understanding.agents.workflows.legacy_review.__init__',
 'doc_understanding.teams.legacy_review.graph': 'doc_understanding.agents.workflows.legacy_review.graph',
 'doc_understanding.teams.legacy_review.service': 'doc_understanding.agents.workflows.legacy_review.service',
 'doc_understanding.teams.legacy_review.state': 'doc_understanding.agents.workflows.legacy_review.state',
 'doc_understanding.teams.legacy_review.version': 'doc_understanding.agents.workflows.legacy_review.version',
 'doc_understanding.teams.registry': 'doc_understanding.agents.workflows.registry',
 'doc_understanding.teams.semantic_campaign.__init__': 'doc_understanding.agents.workflows.semantic_campaign.__init__',
 'doc_understanding.teams.semantic_campaign.__main__': 'doc_understanding.agents.workflows.semantic_campaign.__main__',
 'doc_understanding.teams.semantic_campaign.inventory': 'doc_understanding.agents.workflows.semantic_campaign.inventory',
 'doc_understanding.teams.semantic_campaign.report': 'doc_understanding.agents.workflows.semantic_campaign.report',
 'doc_understanding.teams.semantic_campaign.service': 'doc_understanding.agents.workflows.semantic_campaign.service',
 'doc_understanding.teams.table_review.__init__': 'doc_understanding.agents.workflows.table_review.__init__',
 'doc_understanding.teams.table_review.graph': 'doc_understanding.agents.workflows.table_review.graph',
 'doc_understanding.teams.table_review.profiles': 'doc_understanding.agents.workflows.table_review.profiles',
 'doc_understanding.teams.table_review.service': 'doc_understanding.agents.workflows.table_review.service',
 'doc_understanding.teams.table_review.state': 'doc_understanding.agents.workflows.table_review.state',
 'doc_understanding.teams.table_review.version': 'doc_understanding.agents.workflows.table_review.version',
 'doc_understanding.document_rag.__init__': 'doc_understanding.rag.__init__',
 'doc_understanding.document_rag.__main__': 'doc_understanding.rag.__main__',
 'doc_understanding.document_rag.answer': 'doc_understanding.rag.answer',
 'doc_understanding.document_rag.batch': 'doc_understanding.rag.batch',
 'doc_understanding.document_rag.constraints': 'doc_understanding.rag.constraints',
 'doc_understanding.document_rag.graph': 'doc_understanding.rag.graph',
 'doc_understanding.document_rag.html_adapter': 'doc_understanding.parser.extraction.html_adapter',
 'doc_understanding.document_rag.index': 'doc_understanding.rag.index',
 'doc_understanding.document_rag.judge_telemetry': 'doc_understanding.rag.judge_telemetry',
 'doc_understanding.document_rag.model_policy': 'doc_understanding.rag.model_policy',
 'doc_understanding.document_rag.narrative': 'doc_understanding.rag.narrative',
 'doc_understanding.document_rag.nested_content': 'doc_understanding.parser.assembly.nested_content',
 'doc_understanding.document_rag.nested_evidence': 'doc_understanding.rag.nested_evidence',
 'doc_understanding.document_rag.repository': 'doc_understanding.rag.repository',
 'doc_understanding.document_rag.service': 'doc_understanding.rag.service',
 'doc_understanding.document_rag.telemetry': 'doc_understanding.runtime.telemetry',
 'doc_understanding.document_rag.text_grid': 'doc_understanding.parser.structure.text_grid',
 'doc_understanding.document_rag.trace_reader': 'doc_understanding.rag.trace_reader',
 'doc_understanding.document_rag.understanding': 'doc_understanding.rag.understanding',
 'doc_understanding.eval.__init__': 'doc_understanding.evaluation.__init__',
 'doc_understanding.eval.aggregation_arithmetic': 'doc_understanding.validation.arithmetic',
 'doc_understanding.eval.golden': 'doc_understanding.evaluation.parser.golden',
 'doc_understanding.eval.ledger': 'doc_understanding.evaluation.ledger',
 'doc_understanding.eval.physical': 'doc_understanding.parser.physical.runner',
 'doc_understanding.eval.sample_unseen': 'doc_understanding.evaluation.sample_unseen',
 'doc_understanding.eval.semantic': 'doc_understanding.services.structure_review',
 'doc_understanding.eval.tree_accuracy': 'doc_understanding.evaluation.parser.tree_accuracy',
 'doc_understanding.human_review.__init__': 'doc_understanding.review.human.__init__',
 'doc_understanding.human_review.app': 'doc_understanding.review.human.app',
 'doc_understanding.human_review.precheck': 'doc_understanding.review.human.precheck',
 'doc_understanding.human_review.store': 'doc_understanding.review.human.store',
 'doc_understanding.ingestion.__init__': 'doc_understanding.storage.ingestion.__init__',
 'doc_understanding.ingestion.artifacts': 'doc_understanding.storage.ingestion.artifacts',
 'doc_understanding.parser_experiments.__init__': 'doc_understanding.experiments.parser_repair.__init__',
 'doc_understanding.parser_experiments.automatic': 'doc_understanding.experiments.parser_repair.automatic',
 'doc_understanding.parser_experiments.engine': 'doc_understanding.experiments.parser_repair.engine',
 'doc_understanding.parser_experiments.parser_refresh': 'doc_understanding.experiments.parser_repair.parser_refresh',
 'doc_understanding.parser_experiments.queue': 'doc_understanding.experiments.parser_repair.queue',
 'doc_understanding.parser_experiments.registered_plan': 'doc_understanding.experiments.parser_repair.registered_plan',
 'doc_understanding.parser_experiments.source_checks': 'doc_understanding.evaluation.parser.reference_checks',
 'doc_understanding.persistence.__init__': 'doc_understanding.storage.persistence.__init__',
 'doc_understanding.persistence.base': 'doc_understanding.storage.persistence.base',
 'doc_understanding.physical_experiment.__init__': 'doc_understanding.experiments.physical_recovery.__init__',
 'doc_understanding.physical_experiment.fill_boundaries': 'doc_understanding.parser.physical.fill_boundaries',
 'doc_understanding.physical_experiment.recovery': 'doc_understanding.experiments.physical_recovery.recovery',
 'doc_understanding.physical_experiment.text_grid': 'doc_understanding.experiments.physical_recovery.text_grid',
 'doc_understanding.semantic_description.__init__': 'doc_understanding.review.semantic_description.__init__',
 'doc_understanding.semantic_description.diagnostics': 'doc_understanding.review.semantic_description.diagnostics',
 'doc_understanding.semantic_description.guidance': 'doc_understanding.review.semantic_description.guidance',
 'doc_understanding.semantic_description.models': 'doc_understanding.review.semantic_description.models',
 'doc_understanding.semantic_description.numeric': 'doc_understanding.review.semantic_description.numeric',
 'doc_understanding.semantic_description.rounding': 'doc_understanding.review.semantic_description.rounding',
 'doc_understanding.semantic_description.routes': 'doc_understanding.review.semantic_description.routes',
 'doc_understanding.semantic_description.store': 'doc_understanding.review.semantic_description.store',
 'doc_understanding.staging.__init__': 'doc_understanding.storage.staging.__init__',
 'doc_understanding.staging.layout': 'doc_understanding.parser.layout',
 'doc_understanding.staging.local': 'doc_understanding.storage.staging.local',
 'doc_understanding.staging.prepare': 'doc_understanding.parser.pipeline',
 'doc_understanding.staging.role_proposals': 'doc_understanding.agents.role_proposals',
 'doc_understanding.staging.verify': 'doc_understanding.storage.staging.verify'}
# 승인된 표시 파일 이동만 등록한다. 원본 ZIP·해시와 AST 비교 규칙은 유지한다.
RELOCATIONS['doc_understanding.web.document_view'] = 'doc_understanding.review.presentation.document'
REVERSE = {new: old for old, new in RELOCATIONS.items()}


def source_module(path):
    return path.removeprefix('src/').removesuffix('.py').replace('/', '.')


def relocated_path(path):
    module = source_module(path)
    target = RELOCATIONS.get(module)
    return 'src/' + target.replace('.', '/') + '.py' if target else path


def normalized_source(raw, module):
    """주석은 제외하고 import 목적지를 이전 이름으로 환산하여 실행 AST를 비교한다."""
    tree = ast.parse(raw.decode('utf-8-sig'))
    package = module.rsplit('.', 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            name = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else (node.module or '')
            node.module = REVERSE.get(name, name)
            node.level = 0
    return ast.dump(tree, include_attributes=False)


def equivalent_source(before, after, old_path, new_path):
    try:
        return normalized_source(before, source_module(old_path)) == normalized_source(after, source_module(new_path))
    except (SyntaxError, UnicodeError, ImportError, ValueError):
        return False


def check(root):
    folder = root / 'reports/parser-freeze-20261105'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf8'))
    archive_name = 'reports/parser-freeze-20261105/baseline.zip'
    exact = {**manifest['artifact_hashes'], archive_name: manifest['archive_sha256']}
    changed = [name for name, digest in exact.items()
               if not (root / name).is_file()
               or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    equivalent = []
    # 기준 ZIP의 해시가 다르면 비교 원본으로 신뢰하지 않는다.
    archive_ok = archive_name not in changed
    archive = zipfile.ZipFile(root / archive_name) if archive_ok else None
    try:
        for old_path, digest in manifest['protected_hashes'].items():
            new_path = relocated_path(old_path)
            target = root / new_path
            if not target.is_file():
                changed.append(new_path)
                continue
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() == digest:
                continue
            baseline = archive.read(old_path) if archive is not None else None
            if (baseline is not None and hashlib.sha256(baseline).hexdigest() == digest
                    and equivalent_source(baseline, raw, old_path, new_path)):
                equivalent.append(new_path)
            else:
                changed.append(new_path)
        # 새 패키지의 초기화 파일에 실행 로직이 추가되지 않았는지도 검사한다.
        package_paths = {str(Path(relocated_path(name)).parent / '__init__.py').replace('\\', '/')
                         for name in manifest['protected_hashes'] if relocated_path(name) != name}
        # 기존 보호 목록의 초기화 파일은 위에서 원본 해시/AST로 이미 검증했다.
        # 새로 생긴 초기화 파일에만 무실행 규칙을 추가 적용한다.
        package_paths -= {relocated_path(name) for name in manifest['protected_hashes']}
        for name in sorted(package_paths):
            path = root / name
            try:
                if not path.is_file() or ast.parse(path.read_bytes()).body:
                    changed.append(name)
            except (SyntaxError, UnicodeError):
                changed.append(name)
    finally:
        if archive is not None:
            archive.close()
    return {'checked': len(manifest['protected_hashes']) + len(exact),
            'changed_or_missing': sorted(set(changed)),
            'relocated_or_commented_equivalent': equivalent}
