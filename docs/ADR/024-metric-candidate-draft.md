# ADR-024 — MetricCandidate draft; no Fact publication

The five-document staging run reuses ADR-023 without changing source IR contracts,
approval histories or DB schema. It includes the existing Gangdong PDF, plus four
previously exposed development PDFs. Namwon remains development, Nampyeong untouched.

MetricCandidate 0.1-draft is an optional next-layer contract, not an automatic
financial metric extractor. It binds the original processing run, PDF SHA, artifact
SHA, page, original block/cell and bbox. Institution, observation period, displayed
unit and accounting scope need their own evidence; file names alone are insufficient.
Raw value and parsed Decimal are separate. Blank is never converted to zero.
Sign/normalization policy is explicit; missing dimensions remain unresolved.

Core identifiers: total_assets, total_liabilities, total_equity (instant);
operating_revenue, net_income (duration). Source label matching only finds review
locations; it does not establish an accounting definition or scope.
Numeric validation and semantic review are separate. Candidate IDs are not Fact IDs;
human review references cannot grant publication in this draft. Existing Gold and
publication approval workflows remain independent.

Next implementation: original label/value pairing → header/period/unit context
binding → scope/sign normalization → duplicate/conflict checks → human approval
adapter → separately authorized Fact publication. Staging JSONB can later retain a
MetricCandidate artifact; this phase does not insert proposed metric records as facts.
