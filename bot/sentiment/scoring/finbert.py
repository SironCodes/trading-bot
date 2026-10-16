"""FinBERT (ProsusAI/finbert) on CPU: score = P(positive) - P(negative), confidence = top probability."""
from bot.sentiment.scoring.base import SentimentScorer


class FinBert(SentimentScorer):
    name = "finbert"
    knowledge_cutoff = None  # a fine-tuned classifier, not a generative LLM: it cannot know later prices

    def __init__(self, model: str, device: str = "cpu", batch_size: int = 16):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForSequenceClassification.from_pretrained(model).to(device).eval()
        self.device, self.batch_size = device, batch_size
        labels = {v.lower(): k for k, v in self.model.config.id2label.items()}
        self.pos, self.neg = labels["positive"], labels["negative"]

    def score(self, texts):
        import torch

        out = []
        for i in range(0, len(texts), self.batch_size):
            enc = self.tokenizer(texts[i:i + self.batch_size], padding=True, truncation=True, return_tensors="pt")
            with torch.no_grad():
                probs = self.model(**enc.to(self.device)).logits.softmax(-1).tolist()
            out += [(p[self.pos] - p[self.neg], max(p)) for p in probs]
        return out
