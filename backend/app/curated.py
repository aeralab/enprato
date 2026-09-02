from __future__ import annotations

import os
from typing import Any


def curated_enabled() -> bool:
    return os.environ.get("ENPRATO_ENABLE_CURATED", "").strip().lower() in {"1", "true", "yes"}


# VOA Learning English · Let's Learn English Level 2（公开网页链接，点选后按用户各自 prepare-url）
VOA_LEVEL2: list[dict[str, Any]] = [
    {
        "id": "voa-l2-01",
        "lesson": 1,
        "title": "Let's Learn English Level 2 · Lesson 1",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson1/3960391.html",
    },
    {
        "id": "voa-l2-02",
        "lesson": 2,
        "title": "Let's Learn English Level 2 · Lesson 2",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson-2/3960471.html",
    },
    {
        "id": "voa-l2-03",
        "lesson": 3,
        "title": "Let's Learn English Level 2 · Lesson 3",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson-3/4027340.html",
    },
    {
        "id": "voa-l2-04",
        "lesson": 4,
        "title": "Let's Learn English Level 2 · Lesson 4 · Run Away with the Circus",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson-4-run-away-with-the-circus/4034187.html",
    },
    {
        "id": "voa-l2-05",
        "lesson": 5,
        "title": "Let's Learn English Level 2 · Lesson 5 · Greatest Vacation",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson-5-greatest-vacation/4035571.html",
    },
    {
        "id": "voa-l2-06",
        "lesson": 6,
        "title": "Let's Learn English Level 2 · Lesson 6 · Will It Float?",
        "source_url": "https://learningenglish.voanews.com/a/lesson-6-will-it-float/4064553.html",
    },
    {
        "id": "voa-l2-07",
        "lesson": 7,
        "title": "Let's Learn English Level 2 · Lesson 7 · Tip Your Tour Guide",
        "source_url": "https://learningenglish.voanews.com/a/lesson-7-tip-your-tour-guide/4064769.html",
    },
    {
        "id": "voa-l2-08",
        "lesson": 8,
        "title": "Let's Learn English Level 2 · Lesson 8 · Best Barbecue",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson-8-best-barbecue/4073994.html",
    },
    {
        "id": "voa-l2-09",
        "lesson": 9,
        "title": "Let's Learn English Level 2 · Lesson 9 · Pets Are Family Too",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-level-2-lesson-9-pets-are-family-too/4074883.html",
    },
    {
        "id": "voa-l2-10",
        "lesson": 10,
        "title": "Let's Learn English Level 2 · Lesson 10 · Visit to Peru",
        "source_url": "https://learningenglish.voanews.com/a/lets-learn-english-lesson-10-visit-to-peru/4079037.html",
    },
]


def list_curated_lessons() -> list[dict[str, Any]]:
    if not curated_enabled():
        return []
    return [
        {
            "id": item["id"],
            "lesson": item["lesson"],
            "title": item["title"],
            "source_url": item["source_url"],
            "series": "VOA Let's Learn English Level 2",
        }
        for item in VOA_LEVEL2
    ]
