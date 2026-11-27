# 역할: OpenAI Responses API로 요청 하나를 보내는 전송 함수. 파서·에이전트·RAG가 함께 쓰는 가장 아래 계층이다.
"""호출 정책·예산·기록은 호출하는 쪽이 맡는다. 여기서는 요청을 보내고 응답 JSON을 돌려주기만 한다."""
import json
import urllib.request

RESPONSES_URL = "https://api.openai.com/v1/responses"


def http_transport(payload, key, timeout):
    request = urllib.request.Request(
        RESPONSES_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())
