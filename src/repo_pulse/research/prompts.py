from repo_pulse.research.base import ResearchRequest


def build_research_prompt(request: ResearchRequest) -> str:
    evidence_block = (
        request.evidence.to_prompt_block() if request.evidence else "仓库一手证据：信息不足以确认"
    )
    return f"""
你是面向中文工程团队的技术研究助手，允许保留英文术语（如 API、SDK、release notes）。

仓库：{request.full_name}
仓库链接：{request.repo_url}
{evidence_block}

要求：
1) 可使用公开网络资料，但优先使用 evidence 中的一手资料；官方信息优先官方仓库页和官方文档。
2) 当 evidence 与公开网络资料冲突时，必须明确标注冲突点与不确定项，不要静默合并。
3) citations 优先官方来源（仓库 / docs / blog / release notes），社区来源仅作补充并明确标注。
4) 回答必须是严格 JSON，不允许输出额外解释文本。
5) JSON 结构固定为：
{{
  "what_it_is": "字符串",
  "why_now": "字符串",
  "fit_for": "字符串",
  "not_for": "字符串",
  "trial_verdict": "can_run_locally | needs_api_key | needs_cloud_resource | needs_complex_setup | source_reading_only | insufficient_information",
  "trial_requirements": [
    {{
      "label": "字符串",
      "detail": "字符串",
      "source": "字符串",
      "source_url": "字符串，可选"
    }}
  ],
  "trial_time_estimate": "字符串",
  "quickstart_steps": [
    {{
      "label": "字符串",
      "action": "字符串",
      "commands": [
        {{
          "language": "字符串，可选，默认 bash",
          "code": "字符串"
        }}
      ],
      "expected_result": "字符串",
      "source": "字符串",
      "source_url": "字符串，可选"
    }}
  ],
  "success_signal": "字符串",
  "common_blockers": [
    {{
      "label": "字符串",
      "detail": "字符串",
      "source": "字符串",
      "source_url": "字符串，可选"
    }}
  ],
  "best_practices": ["字符串"],
  "risks": ["字符串"],
  "citations": [
    {{"title": "字符串", "url": "字符串", "snippet": "字符串，可选"}}
  ],
  "metadata": {{
    "provider": "字符串",
    "model": "字符串",
    "generated_at": "ISO8601 字符串",
    "batch_id": "字符串"
  }}
}}
6) quickstart 已移除，必须改用 quickstart_steps；不要输出 quickstart 字段。
7) trial_requirements 与 common_blockers 必须是包含 label、detail、source 的对象数组；如能确认来源链接，可补充 source_url，否则写空字符串。
8) quickstart_steps 必须是包含 label、action、expected_result、source 的对象数组，并且必须给出最短且现实可行的首次运行路径；如能确认精确命令，请填入 commands 数组，否则留空数组。
9) 仅在仓库材料或权威公开来源明确支持时才能写出具体命令；优先参考官方文档与仓库作者提供的示例。
10) 不能确认命令时，必须明确写“信息不足以确认”，不要编造命令、脚本或运行步骤。
11) 阻塞项必须放入 common_blockers，不要埋在 risks；risks 仅用于更广义的工程风险。
12) trial_verdict、quickstart_steps、success_signal 三者必须相互一致，不得互相矛盾。
13) 内容强调工程落地，结论适配中文工程团队阅读习惯。
14) 如果某一项无法确认，请明确写出“信息不足以确认”，不要省略字段。
""".strip()
