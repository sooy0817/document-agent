$ErrorActionPreference = 'Stop'
# 기존 postgres_data 볼륨은 유지한다. 원격 DB나 별도 실험 DB를 변경하지 않는다.
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
docker compose up -d postgres
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL startup failed.' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    docker compose exec -T postgres pg_isready -U nh_document -d nh_document
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'Local PostgreSQL did not become ready.' }
& .\.venv\Scripts\python.exe -X utf8 -m doc_understanding.rag embedding-migrate
if ($LASTEXITCODE -ne 0) { throw 'Embedding schema migration failed.' }
# 실제 pgvector 타입·코사인 연산을 확인하며 문서나 모델 API를 호출하지 않는다.
docker compose exec -T postgres psql -X -U nh_document -d nh_document -v ON_ERROR_STOP=1 -c "SELECT extversion FROM pg_extension WHERE extname='vector'; SELECT 1-('[1,0,0]'::vector <=> '[1,0,0]'::vector) AS identical_vector_similarity;"
if ($LASTEXITCODE -ne 0) { throw 'Vector smoke check failed.' }
