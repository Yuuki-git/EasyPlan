export function parseExplorationSummary(summary: string | null | undefined) {
  const result = {
    judgment: '进行低成本验证，降低不确定性',
    basis: '当前信息不足或面临较高执行门槛，需优先厘清担忧与收益。',
    exploration: '建议开展澄清、调研或低成本测试任务以获得更多决策依据。'
  };

  if (!summary) return result;

  const text = summary.trim();

  const judgmentRegex = /(?:当前判断|判断|结论)[:：\s]*-?\s*(.*?)(?=(?:判断依据|依据|下一步探索|探索路线|探索建议)[:：\s]|$)/is;
  const basisRegex = /(?:判断依据|依据|分析)[:：\s]*-?\s*(.*?)(?=(?:下一步探索|探索路线|探索建议|当前判断)[:：\s]|$)/is;
  const explorationRegex = /(?:下一步探索|探索路线|探索建议|探索)[:：\s]*-?\s*(.*?)(?=(?:当前判断|判断依据|依据)[:：\s]|$)/is;

  const mJudgment = text.match(judgmentRegex);
  const mBasis = text.match(basisRegex);
  const mExploration = text.match(explorationRegex);

  let parsedAny = false;
  if (mJudgment && mJudgment[1]) {
    result.judgment = mJudgment[1].trim();
    parsedAny = true;
  }
  if (mBasis && mBasis[1]) {
    result.basis = mBasis[1].trim();
    parsedAny = true;
  }
  if (mExploration && mExploration[1]) {
    result.exploration = mExploration[1].trim();
    parsedAny = true;
  }

  if (!parsedAny) {
    const sentences = text.split(/(?<=[。！？\n])\s*/).filter(Boolean);
    if (sentences.length > 0) {
      if (sentences.length === 1) {
        result.judgment = sentences[0];
      } else if (sentences.length === 2) {
        result.judgment = sentences[0];
        result.exploration = sentences[1];
      } else {
        result.judgment = sentences.slice(0, Math.min(2, sentences.length - 2)).join('');
        result.basis = sentences.slice(Math.min(2, sentences.length - 2), -1).join('');
        result.exploration = sentences[sentences.length - 1];
      }
    }
  }

  return result;
}
