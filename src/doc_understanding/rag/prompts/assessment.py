# 역할: 질문의 요구 사항과 근거 충족 여부를 판단하도록 모델에 지시한다.
# 프롬프트는 모델 지시문이며, 응답 검증과 모델 호출은 담당하지 않는다.
ASSESSMENT_INSTRUCTIONS = '''
Return assessment with the EXACT original question and explicit requirements.
Interpret the complete question in its language, without relying on a domain vocabulary.
Cover every requested subject, attribute, period, comparison, condition and exception.
Preserve what the user asks the answer to provide. A name is not a procedure, eligibility is
not an amount, and a description is not a reason. Do not add an OR alternative to make available
evidence sufficient. If answer_request is supplied, include its question_quote and requirement
VERBATIM as one assessment requirement. It was fixed before retrieval; do not rewrite or omit it.
Check that the answer actually supplies that requested attribute, not merely related information.
Each requirement.question_quote must be a verbatim nonempty span of the question;
requirement describes its meaning. Split independent obligations; do not omit a difficult one.
For each requirement decide supported/unsupported/ambiguous using only supplied sources.
For supported requirements provide supports: source_id, field (text_raw/row_path/column_path),
and an exact nonempty quote in that field. Path quotes must occur inside ONE path label.
Every source has its own text: a heading, scope, parent, note or unit quote must cite the
source_id of that context source (kind scope_context/parent_text/note/unit_context), never
the table cell that carries it.
Use subject/attribute/period anchors together where needed: an isolated year is not support
for an unrelated measure. Keep document, subject, unit, scope and period aligned per answer.
Text-valued table cells and numbers in ordinary prose are both legitimate evidence.
Do not infer observation periods from publication dates. Relative dates need an explicit
reference and resolution supported by supplied context; otherwise mark ambiguous.
Consider ALL supplied surrounding notes and conditions. Preserve applicable qualifications,
exceptions and conflicting evidence; a shared parent does not prove applicability.
For a list item, read its attached scope_context heading together with the item. A heading
such as "exclusions" plus the listed condition can explicitly state exclusion; the item need
not repeat the heading's predicate. Cite BOTH original sources. Do not treat the absence of
that repeated predicate in the item as missing external evidence. This does not authorize
transferring a condition to unrelated sibling items or inferring facts from a heading alone.
Mark requires_external_verification=true when answering needs present-day validity, external
facts, human approval or other verification absent from the supplied document evidence.
Even when a question uses words such as 'current', historical quotations can be answered
if it explicitly asks what the document states; decide by meaning, not a word match.
Missing, contradictory or unresolved support must lead to abstained=true and empty answer.
A supported assessment requires all requirements supported, anchored in the evidence actually
used in the answer, and requires_external_verification=false. Every selected evidence item
must support a requirement. Report uncertainty; this assessment is not human validation.
'''
