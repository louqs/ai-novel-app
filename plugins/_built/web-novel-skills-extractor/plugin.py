from typing import Any

from core.plugin_manager import PluginManifest
from core.quality_gate import IPipelineContributor


class WebNovelSkillsExtractor(IPipelineContributor):
    """从网络文学作品中提炼写作技能，并在编辑优化时给出建议."""

    name = "web-novel-skills-extractor"
    order = 60

    async def on_load(self, kernel):
        self._kernel = kernel

    async def on_unload(self):
        pass

    async def analyze(self, content: str, context: dict[str, Any]) -> dict[str, Any]:
        """分析文本的写作技能水平，给出改进建议."""
        try:
            # 截取分析片段（避免 token 过长）
            sample = content[:2000] if len(content) > 2000 else content

            messages = [
                {"role": "system", "content": (
                    "你是一位资深网文编辑，擅长分析网络文学的写作技巧。"
                    "请从以下维度分析文本并给出改进建议：\n"
                    "1. 开篇吸引力（黄金三章/黄金一章技巧）\n"
                    "2. 节奏控制（爽点分布、冲突密度）\n"
                    "3. 对话技巧（信息量、人物区分度）\n"
                    "4. 场景描写（感官细节、氛围营造）\n"
                    "5. 钩子设计（章末悬念、翻页驱动力）\n\n"
                    "返回格式：\n"
                    "总结: <1-2句话概括>\n"
                    "问题: <问题1>|<问题2>|...\n"
                    "建议: <建议1>|<建议2>|...\n"
                    "评分: <0-100整数>"
                )},
                {"role": "user", "content": f"请分析以下文本的写作技巧：\n\n{sample}"},
            ]
            response = await self._kernel.call_llm(messages, tier="budget")
            text = response.get("content", "")

            # 解析结构化结果
            summary = ""
            issues = []
            suggestions = []
            score = None

            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("总结:") or line.startswith("总结："):
                    summary = line.split(":", 1)[-1].split("：", 1)[-1].strip()
                elif line.startswith("问题:") or line.startswith("问题："):
                    raw = line.split(":", 1)[-1].split("：", 1)[-1].strip()
                    issues = [s.strip() for s in raw.split("|") if s.strip()]
                elif line.startswith("建议:") or line.startswith("建议："):
                    raw = line.split(":", 1)[-1].split("：", 1)[-1].strip()
                    suggestions = [s.strip() for s in raw.split("|") if s.strip()]
                elif line.startswith("评分:") or line.startswith("评分："):
                    try:
                        score = int(line.split(":", 1)[-1].split("：", 1)[-1].strip())
                    except ValueError:
                        pass

            if not summary:
                summary = text[:100] if text else "分析完成"

            return {"summary": summary, "issues": issues, "suggestions": suggestions, "score": score}
        except Exception as e:
            return {"summary": f"分析失败: {e}", "issues": [], "suggestions": [], "score": None}


def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="web-novel-skills-extractor",
        version="0.2.0",
        description="从网络文学作品中提炼写作技能，在编辑优化时给出建议。",
        dependencies=[],
        hooks=["on_load"],
    )


def create_plugin():
    return WebNovelSkillsExtractor()
