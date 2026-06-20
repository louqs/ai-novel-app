from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

class WebNovelSkillsExtractor:
    name = "web-novel-skills-extractor"
    version = "0.1.0"

    async def on_load(self, kernel):
        self._kernel = kernel

    async def on_unload(self):
        pass

    async def extract_skills_from_web_novels(self, novel_text: str) -> dict:
        """
        从网络文学作品中提炼出优秀的写作技能和风格。
        """
        try:
            messages = [
                {"role": "system", "content": "你是一位专业的文学编辑，任务是从给定的文本中提炼出优秀的写作技能和风格。"},
                {"role": "user", "content": f"请从以下文本中提炼出优秀的写作技能和风格：\n\n{novel_text}"},
            ]
            response = await self._kernel.call_llm(messages, tier="standard")
            return {"skills": response["content"]}
        except Exception as e:
            self._kernel.get_logger("web-novel-skills-extractor").error(f"提取技能时发生错误: {e}")
            return {"skills": "提取技能时发生错误"}

    async def generate_similar_content(self, skills: dict, prompt: str) -> dict:
        """
        根据提炼出的写作技能生成类似的内容。
        """
        try:
            messages = [
                {"role": "system", "content": "你是一位专业的作家，任务是根据给定的写作技能生成类似的内容。"},
                {"role": "user", "content": f"请根据以下写作技能生成类似的内容：\n\n{skills['skills']}\n\n提示：{prompt}"},
            ]
            response = await self._kernel.call_llm(messages, tier="standard")
            return {"generated_content": response["content"]}
        except Exception as e:
            self._kernel.get_logger("web-novel-skills-extractor").error(f"生成内容时发生错误: {e}")
            return {"generated_content": "生成内容时发生错误"}

def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="web-novel-skills-extractor",
        version="0.1.0",
        description="从网络文学作品中提炼出优秀的写作技能和风格。",
        dependencies=[],
        hooks=["on_load"],
    )

def create_plugin():
    return WebNovelSkillsExtractor()