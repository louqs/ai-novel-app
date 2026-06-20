from core.plugin_manager import PluginManifest
from core.quality_gate import GateIssue, GateResult, GateVerdict, IQualityGate, Severity

class BattleVividnessChecker(IQualityGate):
    name = "battle-vividness-checker"
    version = "0.1.0"
    order = 50  # 门禁执行顺序，越小越先

    async def on_load(self, kernel):
        self._kernel = kernel

    async def on_unload(self):
        pass

    async def evaluate(self, chapter: dict, context: dict) -> GateResult:
        content = chapter.get("content", "")
        
        try:
            # 调用 LLM 分析战斗场面的生动性
            messages = [
                {"role": "system", "content": "你是一个小说质量检查助手，专门检查战斗场面的生动性。"},
                {"role": "user", "content": f"请分析以下文本中的战斗场面是否生动：\n{content}"},
                {"role": "assistant", "content": "好的，我将分析这段文本。"}
            ]
            response = await self._kernel.call_llm(messages, tier="standard")
            analysis = response["content"]

            # 解析 LLM 的反馈
            if "平淡" in analysis or "不够生动" in analysis:
                issues = [GateIssue(severity=Severity.WARNING, code="battle_vividness.flat", message="战斗场面描写较为平淡，建议增加更多细节和感官描述以提高生动性。")]
                verdict = GateVerdict.REVISE
                score = 0.5
            else:
                issues = []
                verdict = GateVerdict.PASS
                score = 0.8

            return GateResult(
                gate_name=self.name,
                verdict=verdict,
                issues=issues,
                score=score,
            )
        except Exception as e:
            # 记录错误日志
            logger = self._kernel.get_logger("battle-vividness-checker")
            logger.error(f"插件 {self.name} 执行时发生错误: {str(e)}")
            return GateResult(
                gate_name=self.name,
                verdict=GateVerdict.ERROR,
                issues=[GateIssue(severity=Severity.ERROR, code="battle_vividness.error", message=f"插件执行时发生错误: {str(e)}")],
                score=0.0,
            )

def create_manifest() -> PluginManifest:
    return PluginManifest(
        name="battle-vividness-checker",
        version="0.1.0",
        description="检测战斗场面描写是否生动的插件",
        dependencies=[],
        hooks=["on_load", "on_unload", "on_gate_check"],  # 门禁插件必须包含 on_gate_check
    )

def create_plugin():
    return BattleVividnessChecker()