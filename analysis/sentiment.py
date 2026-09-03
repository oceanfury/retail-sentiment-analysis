# -*- coding: utf-8 -*-
"""
词典法情绪分析引擎 v2
改进：歧义词上下文规则 + 信息帖识别 + 词典扩充 + 疑问句检测 + 广告帖过滤
"""
import json
import re
from typing import Dict, List
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import SENTIMENT_DICT_PATH


class SentimentAnalyzer:
    """情绪分析器 v2"""

    def __init__(self, dict_path: str = None):
        if dict_path is None:
            dict_path = SENTIMENT_DICT_PATH
        self.dict_path = dict_path
        self._load_dictionary()

    def _load_dictionary(self):
        with open(self.dict_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.positive_words = set(data.get("positive_words", []))
        self.negative_words = set(data.get("negative_words", []))
        self.neutral_words = set(data.get("neutral_words", []))
        self.intensifier_words = data.get("intensifier_words", {})
        self.negation_words = set(data.get("negation_words", []))
        self.degree_words = data.get("degree_words", {})
        self.ambiguous_words = data.get("ambiguous_words", {})
        self.info_patterns = data.get("info_patterns", [])
        self.ad_patterns = data.get("ad_patterns", [])
        self.question_patterns = data.get("question_patterns", [])

        self._positive_sorted = sorted(self.positive_words, key=len, reverse=True)
        self._negative_sorted = sorted(self.negative_words, key=len, reverse=True)
        self._intensifier_sorted = sorted(self.intensifier_words.keys(), key=len, reverse=True)
        self._negation_sorted = sorted(self.negation_words, key=len, reverse=True)
        self._degree_sorted = sorted(self.degree_words.keys(), key=len, reverse=True)

    def analyze(self, text: str) -> Dict:
        if not text or not text.strip():
            return self._empty_result()

        text = text.strip()

        is_info = self._is_info_post(text)
        is_ad = self._is_ad_post(text)

        pos_score = 0.0
        neg_score = 0.0
        matched_positive = []
        matched_negative = []
        has_question = False

        sentences = re.split(r"[。！？!?.\n]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        for sentence in sentences:
            if self._is_question(sentence):
                has_question = True

            sent_result = self._analyze_sentence(sentence)
            pos_score += sent_result["pos_score"]
            neg_score += sent_result["neg_score"]
            matched_positive.extend(sent_result["positive_words"])
            matched_negative.extend(sent_result["negative_words"])

        total_score = pos_score - neg_score
        total_intensity = abs(pos_score) + abs(neg_score)

        # 加基准值，防止单个弱情感词被归一化为 ±1.0
        if total_intensity > 0:
            normalized_score = total_score / (total_intensity + 0.5)
            normalized_score = max(-1.0, min(1.0, normalized_score))
        else:
            normalized_score = 0.0

        # 价格预测检测："到XXX元" 视为看多
        if re.search(r"到\d+元|会到\d+", text):
            pos_score += 0.3
            normalized_score = max(normalized_score, 0.3)

        # 信息帖 → 强制中性
        if is_info:
            normalized_score *= 0.1

        # 广告帖 → 强制中性
        if is_ad:
            normalized_score = 0.0

        if normalized_score > 0.2:
            sentiment = "positive"
        elif normalized_score < -0.2:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        word_count = len(matched_positive) + len(matched_negative)
        confidence = min(1.0, total_intensity * 0.3 + word_count * 0.1)

        # 疑问句降低置信度
        if has_question:
            confidence *= 0.5

        if is_info or is_ad:
            confidence *= 0.3

        return {
            "sentiment": sentiment,
            "score": round(normalized_score, 4),
            "confidence": round(max(0.0, confidence), 4),
            "positive_count": len(matched_positive),
            "negative_count": len(matched_negative),
            "positive_words": list(set(matched_positive)),
            "negative_words": list(set(matched_negative)),
        }

    def _analyze_sentence(self, sentence: str) -> Dict:
        pos_score = 0.0
        neg_score = 0.0
        matched_positive = []
        matched_negative = []

        # 匹配正向词
        for word in self._positive_sorted:
            if word in sentence:
                count = sentence.count(word)
                modifier = self._get_modifier(sentence, word)
                score_val = count * modifier
                if score_val >= 0:
                    pos_score += score_val
                    matched_positive.append(word)
                else:
                    # 否定翻转 → 转为负面
                    neg_score += abs(score_val)
                    matched_negative.append(f"不{word}")
                sentence = sentence.replace(word, " " * len(word))

        # 歧义词上下文规则
        for word, negative_contexts in self.ambiguous_words.items():
            if word in sentence:
                count = sentence.count(word)
                has_neg_ctx = self._has_negative_context(sentence, word, negative_contexts)
                if has_neg_ctx:
                    neg_score += count * 0.1
                    matched_negative.append(f"{word}(负面语境)")
                else:
                    pos_score += count * 0.05
                    matched_positive.append(word)
                sentence = sentence.replace(word, " " * len(word))

        # 匹配负向词
        for word in self._negative_sorted:
            if word in sentence:
                count = sentence.count(word)
                modifier = self._get_modifier(sentence, word)
                score_val = count * modifier
                if score_val >= 0:
                    neg_score += score_val
                    matched_negative.append(word)
                else:
                    pos_score += abs(score_val)
                    matched_positive.append(f"不{word}")
                sentence = sentence.replace(word, " " * len(word))

        return {
            "pos_score": pos_score,
            "neg_score": neg_score,
            "positive_words": matched_positive,
            "negative_words": matched_negative,
        }

    def _has_negative_context(self, text: str, target_word: str, context_words: List[str]) -> bool:
        pos = text.find(target_word)
        if pos == -1:
            return False
        prefix = text[max(0, pos - 8):pos]
        suffix = text[pos + len(target_word):pos + len(target_word) + 8]
        context = prefix + suffix
        for cw in context_words:
            if cw in context:
                return True
        return False

    def _is_info_post(self, text: str) -> bool:
        for pattern in self.info_patterns:
            if pattern in text:
                return True
        return False

    def _is_ad_post(self, text: str) -> bool:
        for pattern in self.ad_patterns:
            if pattern in text:
                return True
        return False

    def _is_question(self, sentence: str) -> bool:
        # 只检测以问号或疑问词结尾的句子
        stripped = sentence.rstrip("。.!！")
        if stripped.endswith("?") or stripped.endswith("？"):
            return True
        # 检测句尾疑问词
        for pattern in ["吗？", "呢？", "不？", "吗", "呢"]:
            if stripped.endswith(pattern):
                return True
        # 检测"能不能""会不会"等疑问句式
        if "能不能" in sentence or "会不会" in sentence:
            return True
        return False

    def _get_modifier(self, text: str, target_word: str) -> float:
        modifier = 1.0
        pos = text.find(target_word)
        if pos == -1:
            return modifier

        # 缩小窗口到 5 个字符
        prefix = text[max(0, pos - 5):pos]

        # 检查否定词和目标词之间是否有标点符号（逗号等隔断）
        has_punctuation = bool(re.search(r"[，,。.!！？?]", prefix[-2:])) if len(prefix) >= 1 else False

        has_negation = False
        if not has_punctuation:
            for neg_word in self._negation_sorted:
                if neg_word in prefix:
                    has_negation = True
                    break

        degree_modifier = 1.0
        for dw in self._degree_sorted:
            if dw in prefix:
                degree_modifier = self.degree_words[dw]
                break

        for iw in self._intensifier_sorted:
            if iw in prefix:
                degree_modifier *= self.intensifier_words[iw]
                break

        if has_negation:
            modifier = -degree_modifier * 0.8
        else:
            modifier = degree_modifier

        return modifier

    def _empty_result(self) -> Dict:
        return {
            "sentiment": "neutral",
            "score": 0.0,
            "confidence": 0.0,
            "positive_count": 0,
            "negative_count": 0,
            "positive_words": [],
            "negative_words": [],
        }

    def analyze_post(self, post: Dict) -> Dict:
        title = post.get("title", "") or ""
        content = post.get("content", "") or ""
        full_text = f"{title}。{title}。{content}"
        return self.analyze(full_text)

    def batch_analyze(self, posts: List[Dict]) -> List[Dict]:
        results = []
        for post in posts:
            sentiment = self.analyze_post(post)
            post_with_sentiment = {**post, **sentiment}
            results.append(post_with_sentiment)
        return results


_analyzer = None


def get_sentiment_analyzer() -> SentimentAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentAnalyzer()
    return _analyzer


if __name__ == "__main__":
    analyzer = get_sentiment_analyzer()

    test_cases = [
        ("茅台今天大涨，看好后市，继续加仓！", "positive"),
        ("这只股票要暴跌了，赶紧跑，垃圾股一个", "negative"),
        ("今天震荡整理，观望为主", "neutral"),
        ("超跌反弹而已，千万不要上头，套牢盘随时砸得你喊娘", "negative"),
        ("上周五多少倒霉蛋进来抄底了，明天开盘就割了", "negative"),
        ("利好很多，能不能涨是另一回事", "neutral"),
        ("尾盘主力大举出逃3股", "neutral"),
        ("不看好后市", "negative"),
        ("这个股今年会到200元", "positive"),
        ("从人形机器人第一股到全网群嘲", "negative"),
        ("不能理解为什么会跌这么多，底部在哪里？", "negative"),
        ("浪潮信息毛利率改善才是中报重点", "neutral"),
        ("耐心等待一次砸恐慌盘的机会", "negative"),
        ("垃圾拖累板块，亏得裤衩子都不剩", "negative"),
        ("有谁了解浪潮的CPU和GPU自研进度吗？", "neutral"),
        ("看了，画面一般", "neutral"),
    ]

    print("情绪分析 v2 测试：")
    print("=" * 70)
    correct = 0
    for text, expected in test_cases:
        result = analyzer.analyze(text)
        sentiment_cn = {"positive": "看多", "negative": "看空", "neutral": "中性"}[result["sentiment"]]
        expected_cn = {"positive": "看多", "negative": "看空", "neutral": "中性"}[expected]
        match = "✓" if result["sentiment"] == expected else "✗"
        if result["sentiment"] == expected:
            correct += 1
        print(f"\n{match} 文本: {text[:40]}")
        print(f"  结果: {sentiment_cn} (期望: {expected_cn})  分数: {result['score']:.3f}  置信度: {result['confidence']:.3f}")
        if result["positive_words"]:
            print(f"  正向词: {result['positive_words']}")
        if result["negative_words"]:
            print(f"  负向词: {result['negative_words']}")

    print(f"\n准确率: {correct}/{len(test_cases)} ({correct/len(test_cases)*100:.0f}%)")
