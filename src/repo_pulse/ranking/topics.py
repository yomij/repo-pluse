from repo_pulse.schemas import RepositoryCandidate


class TopicClassifier:
    TOPIC_MAP = {
        "ai": {"ai", "llm", "agent", "agents", "rag"},
        "devtools": {"devtools", "cli", "sdk", "tooling"},
        "infra": {"infra", "devops", "kubernetes", "cloud"},
        "frontend": {"frontend", "react", "vue", "nextjs", "fullstack"},
    }
    NOISE_TOPICS = {"template", "boilerplate", "awesome-list"}

    def classify(self, candidate: RepositoryCandidate) -> list[str]:
        haystack = {
            *(topic.lower() for topic in candidate.topics),
            *((candidate.description or "").lower().split()),
        }
        matches = [
            label
            for label, keywords in self.TOPIC_MAP.items()
            if keywords.intersection(haystack)
        ]
        return matches or ["misc"]

    def template_penalty(self, candidate: RepositoryCandidate) -> float:
        candidate_topics = {topic.lower() for topic in candidate.topics}
        if candidate.is_template:
            return 10.0
        return 10.0 if self.NOISE_TOPICS.intersection(candidate_topics) else 0.0
