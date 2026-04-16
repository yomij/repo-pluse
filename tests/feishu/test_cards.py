from repo_pulse.digest.service import DailyDigest
from repo_pulse.feishu.cards import CardBuilder


def test_card_builder_formats_generated_time_in_scheduler_timezone():
    digest = DailyDigest(
        title="GitHub 热门日榜",
        window="24h",
        generated_at="2026-04-14T01:30:00Z",
        entries=[],
    )

    card = CardBuilder().build_digest_card(digest)

    assert (
        card["elements"][-1]["fields"][1]["text"]["content"]
        == "生成时间：2026-04-14 09:30:00"
    )
