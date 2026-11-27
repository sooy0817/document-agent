"""teams / table_review / profiles."""

from doc_understanding.agents.roles import relation_analyst, relation_judge, role_classifier
from doc_understanding.runtime import model_client
from doc_understanding.agents.semantic_resolver import providers
from doc_understanding.agents.workflows.table_review import version


def model_calls(data, *, key, daily_limit):
    # Candidate-by-candidate review can exceed the legacy 4,500-token output ceiling.
    # The version includes this bound so a cached request cannot silently change it.
    def transport(payload, api_key, timeout):
        return providers.http_transport({**payload, "max_output_tokens": 16000}, api_key, timeout)

    return model_client.ModelCalls(
        data,
        key=key,
        daily_limit=daily_limit,
        transport=transport,
        profiles=PROFILES,
        version=version.VERSION,
    )


TEAM = (role_classifier.ROLE, relation_analyst.ROLE, relation_judge.ROLE)
PROFILES = {role.name: role.profile for role in TEAM}
