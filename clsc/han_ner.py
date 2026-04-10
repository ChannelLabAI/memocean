"""
han_ner.py — Lightweight Chinese NER for CLSC v0.6.
Uses jieba for segmentation + POS tagging to extract named entities.
No heavy models (no hanlp, no transformers).
"""
import jieba
import jieba.posseg as pseg

# Suppress jieba initialization messages
import logging
logging.getLogger('jieba').setLevel(logging.ERROR)

# Entity POS tags in jieba that map to named entities
# nr=person name, ns=place name, nt=org/institution, nz=other proper noun
ENTITY_POS = {'nr', 'ns', 'nt', 'nz', 'n'}

def extract_entities(text: str) -> list:
    """
    Extract named entities from Chinese text using jieba POS tagging.
    Returns list of {text, pos, category}.
    """
    words = pseg.cut(text)
    entities = []
    seen = set()
    for word, flag in words:
        if flag in ENTITY_POS and len(word) >= 2 and word not in seen:
            category = {
                'nr': 'person',
                'ns': 'place',
                'nt': 'org',
                'nz': 'proper',
                'n': 'noun',
            }.get(flag, 'other')
            entities.append({'text': word, 'pos': flag, 'category': category})
            seen.add(word)
    return entities

def extract_key_sentences(text: str, n: int = 3) -> list:
    """
    Extract top N key sentences using simple TF scoring.
    Splits on sentence-ending punctuation.
    """
    import re
    sentences = re.split(r'[。！？\n]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    if not sentences:
        return []

    # Score sentences by word frequency
    all_words = list(jieba.cut(' '.join(sentences)))
    word_freq = {}
    for w in all_words:
        if len(w) > 1:
            word_freq[w] = word_freq.get(w, 0) + 1

    scored = []
    for s in sentences:
        score = sum(word_freq.get(w, 0) for w in jieba.cut(s) if len(w) > 1)
        scored.append((score, s))

    scored.sort(reverse=True)
    return [s for _, s in scored[:n]]

if __name__ == "__main__":
    test = "CEO 說 ChannelVenture 的 Term Sheet 已出，COO 確認方向，PM 負責運營。工程師已確認加入。"
    print("Entities:", extract_entities(test))
    print("Key sentences:", extract_key_sentences(test))
