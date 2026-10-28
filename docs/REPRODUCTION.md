# Reproduction guide

## Offline quickstart

Run the commands in the root README. `demo.py` exercises extraction and geometry invariants. `inspect_document.py` uses the current extraction, semantic, CanonicalDocument, search projection, and nested-link functions. Neither invokes a model, a database, or Langfuse.

The generated files retain run identifiers and extraction metadata, so bytes can differ between runs. Do not compare the entire JSON as a deterministic golden file.

## Configured RAG source

The snapshot includes PostgreSQL persistence/search, pgvector retrieval experiments, LangGraph, Langfuse, and GPT-4.1 adapters. These require additional dependencies and a local database/model configuration:

```sh
python -m pip install ".[dev,postgres,llm,graph]"
python -m doc_understanding.document_rag --help
```

This repository does not bundle database contents, production configuration, or live trace endpoints. It has not been revalidated as a clean-install live GPT/database deployment. Historical real runs are described separately in EVALUATION.md. Follow the actual CLI's help and inspect its explicit external-call flags before enabling paid model calls.

## Source snapshot and public scope

The runtime files are copied without algorithm changes from the working project. The local export manifest records their hashes outside the publication directory. The bundled tests are a selected synthetic subset from the earlier core package plus the external-network guard.

The offline demonstration is not an end-to-end benchmark of arbitrary nested PDFs. It intentionally exposes unresolved semantic state rather than modifying evidence to produce a showcase answer.

## Dependencies and publication

A project-wide reuse license has not been selected. No MIT/Apache grant is inferred from the presence of source files. PyMuPDF is an external dependency; consult its official distribution terms for your intended use. Source PDFs and generated private JSON/reports are not included.
