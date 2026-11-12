"""
Fixed item bank for the adaptive part of the placement test (grammar + vocabulary).
8 items per CEFR level (4 grammar, 4 vocabulary). The first option is the key; options are
shuffled per learner on the server, and the key never leaves the server.
A fixed, reviewed bank is used on purpose: placement must be comparable between learners
and must work even when the AI service is down.
"""

from __future__ import annotations

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_RAW = {
    "A1": [
        ("grammar", "She ___ a teacher.", ["is", "are", "am", "be"]),
        ("grammar", "___ you from Kazakhstan?", ["Are", "Is", "Do", "Am"]),
        ("grammar", "I have two ___.", ["brothers", "brother", "brother's", "a brother"]),
        ("grammar", "This is ___ apple.", ["an", "a", "the a", "some"]),
        ("vocabulary", "The opposite of \"big\" is ___.", ["small", "tall", "long", "old"]),
        ("vocabulary", "We usually have breakfast in the ___.", ["morning", "night", "evening", "midnight"]),
        ("vocabulary", "My father's mother is my ___.", ["grandmother", "aunt", "sister", "cousin"]),
        ("vocabulary", "Monday, Tuesday, ___, Thursday.", ["Wednesday", "Friday", "Sunday", "Saturday"]),
    ],
    "A2": [
        ("grammar", "Yesterday we ___ to the cinema.", ["went", "go", "have gone", "were go"]),
        ("grammar", "Look! It ___ outside.", ["is raining", "rains", "rain", "rained"]),
        ("grammar", "My brother is ___ than me.", ["taller", "more tall", "tallest", "the taller"]),
        ("grammar", "There ___ any milk in the fridge.", ["isn't", "aren't", "not is", "no is"]),
        ("vocabulary", "I'd like to ___ a table for two for Friday evening.", ["book", "make up", "take", "write"]),
        ("vocabulary", "It's very cold today — put on your ___.", ["coat", "sunglasses", "shorts", "sandals"]),
        ("vocabulary", "Excuse me, how much does this shirt ___?", ["cost", "pay", "buy", "price"]),
        ("vocabulary", "The train ___ from platform 2 at 7:30.", ["leaves", "gets", "takes", "puts"]),
    ],
    "B1": [
        ("grammar", "I ___ in Astana since 2019.", ["have lived", "live", "am living", "lived"]),
        ("grammar", "If it rains tomorrow, we ___ at home.", ["will stay", "would stay", "stay will", "stayed"]),
        ("grammar", "When I was a child, I ___ play football every day.", ["used to", "use to", "was used to", "am used to"]),
        ("grammar", "This book ___ by millions of people.", ["has been read", "has read", "is reading", "have been read"]),
        ("vocabulary", "Could you ___ the music down? I'm trying to study.", ["turn", "put", "take", "make"]),
        ("vocabulary", "She was ___ when she heard she had won the competition.", ["delighted", "disappointed", "bored", "annoyed"]),
        ("vocabulary", "I'm looking ___ to seeing you next week.", ["forward", "ahead", "after", "for"]),
        ("vocabulary", "He ___ his driving test at the first attempt.", ["passed", "succeeded", "won", "managed"]),
    ],
    "B2": [
        ("grammar", "If I ___ about the problem earlier, I would have helped.", ["had known", "knew", "have known", "would know"]),
        ("grammar", "She asked me where I ___.", ["lived", "did I live", "do I live", "have I lived"]),
        ("grammar", "By the time we arrived, the film ___.", ["had already started", "already started", "has already started", "was already starting"]),
        ("grammar", "I'd rather you ___ smoke in the house.", ["didn't", "don't", "won't", "not"]),
        ("vocabulary", "The company had to ___ 200 workers because of the crisis.", ["lay off", "lay down", "put off", "take off"]),
        ("vocabulary", "His argument was very ___; everyone agreed with him.", ["convincing", "convinced", "convenient", "considerate"]),
        ("vocabulary", "Despite the heavy traffic, we ___ to arrive on time.", ["managed", "succeeded", "could", "achieved"]),
        ("vocabulary", "The new law will have a significant ___ on small businesses.", ["impact", "effort", "affection", "issue"]),
    ],
    "C1": [
        ("grammar", "Not only ___ late, but he also forgot the documents.", ["was he", "he was", "he has been", "did he be"]),
        ("grammar", "___ the weather been better, we would have gone hiking.", ["Had", "If", "Should", "Were"]),
        ("grammar", "It's high time the government ___ action on this issue.", ["took", "takes", "will take", "has taken"]),
        ("grammar", "The report, ___ last week, contains several errors.", ["published", "publishing", "which published", "being publish"]),
        ("vocabulary", "The results were ___ with our initial hypothesis.", ["consistent", "constant", "persistent", "insistent"]),
        ("vocabulary", "The minister's statement was deliberately ___, leaving room for different interpretations.", ["ambiguous", "explicit", "transparent", "accurate"]),
        ("vocabulary", "We need to ___ the risks carefully before making any investment.", ["assess", "access", "assist", "assume"]),
        ("vocabulary", "Her proposal was ___ rejected by the board: not a single member supported it.", ["unanimously", "unanimous", "anonymously", "unequally"]),
    ],
    "C2": [
        ("grammar", "Little ___ that his decision would change the course of history.", ["did he realise", "he realised", "he did realise", "realised he"]),
        ("grammar", "The committee insisted that the proposal ___ reconsidered.", ["be", "is", "was", "will be"]),
        ("grammar", "___ it not for her timely intervention, the project would have collapsed.", ["Were", "Had", "Was", "If"]),
        ("grammar", "So ___ was the demand that the shop ran out of stock within an hour.", ["great", "greatly", "greater", "much"]),
        ("vocabulary", "His attempts to ___ the tension in the room only made matters worse.", ["defuse", "diffuse", "refute", "confuse"]),
        ("vocabulary", "The novel's ending is ___; readers still argue about what it means.", ["enigmatic", "emphatic", "pragmatic", "empathetic"]),
        ("vocabulary", "After years of ___ research, she finally published her findings.", ["painstaking", "painless", "painful", "pained"]),
        ("vocabulary", "The politician tried to ___ the blame for the failure onto his advisers.", ["shift", "lift", "drift", "sift"]),
    ],
}

ITEMS = []
for _level, _rows in _RAW.items():
    for _n, (_skill, _q, _opts) in enumerate(_rows, 1):
        ITEMS.append({"id": f"{_level}-{_n}", "level": _level, "difficulty": LEVELS.index(_level) + 0.5,
                      "skill": _skill, "question": _q, "options": list(_opts), "answer": _opts[0]})
ITEMS_BY_ID = {it["id"]: it for it in ITEMS}

# Productive tasks by level band (low = A1–A2, mid = B1–B2, high = C1–C2).
WRITING_TASKS = {
    "low": {"prompt": "Write about yourself: your family, your home and what you like to do in your free time.",
            "words": "40–70"},
    "mid": {"prompt": "Some people prefer to study online, others prefer a classroom. Which do you prefer and why? "
                      "Give examples from your own experience.", "words": "100–150"},
    "high": {"prompt": "\"Artificial intelligence will make many university courses unnecessary.\" "
                       "To what extent do you agree? Support your view with reasons and examples.", "words": "150–220"},
}
SPEAKING_QUESTIONS = {
    "low": ["Tell me about your family.", "What do you usually do at the weekend?"],
    "mid": ["Describe a place in your country that you would recommend to a tourist, and explain why.",
            "Do you think people spend too much time on their phones these days? Why or why not?"],
    "high": ["Some people say that technology has made us less sociable. How far do you agree?",
             "Talk about a difficult decision you once had to make. What did you consider, and would you decide differently now?"],
}


def band(level: str) -> str:
    return "low" if level in ("A1", "A2") else "mid" if level in ("B1", "B2") else "high"
