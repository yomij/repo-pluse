from repo_pulse.digest.service import DailyDigest


class CardBuilder:
    def build_digest_card(self, digest: DailyDigest) -> dict:
        elements: list[dict] = []
        for entry in digest.entries:
            elements.append(
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": False,
                            "text": {
                                "tag": "lark_md",
                                "content": (
                                    f"**{entry.full_name}**\n"
                                    f"分类：{entry.category}\n"
                                    f"{entry.summary}\n"
                                    f"上榜理由：{entry.reason}"
                                ),
                            },
                        }
                    ],
                }
            )
            actions = [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看详情"},
                    "type": "primary",
                    "value": {
                        "repo": entry.full_name,
                        "action": "detail",
                        "detail_action_value": entry.detail_action_value,
                    },
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看仓库"},
                    "type": "default",
                    "multi_url": {"url": entry.repo_url},
                },
            ]
            if entry.doc_url:
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开文档"},
                        "type": "default",
                        "multi_url": {"url": entry.doc_url},
                    }
                )
            elements.append(
                {
                    "tag": "action",
                    "actions": actions,
                }
            )
        footer_fields = [
            {
                "is_short": True,
                "text": {"tag": "plain_text", "content": f"数据窗口：{digest.window}"},
            },
            {
                "is_short": True,
                "text": {
                    "tag": "plain_text",
                    "content": f"生成时间：{digest.generated_at or '未提供'}",
                },
            },
        ]
        elements.append({"tag": "div", "fields": footer_fields})
        return {
            "header": {"title": {"tag": "plain_text", "content": f"{digest.title} · {digest.window}"}},
            "elements": elements,
        }
