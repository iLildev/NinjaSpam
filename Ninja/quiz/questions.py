"""
quiz/questions.py — Guessing Game Question Bank.

Categories:
  Anime — Guess the anime character
  Cars  — Guess the car
"""

from __future__ import annotations
from typing import TypedDict


class Question(TypedDict):
    category: str
    clue: str
    answers: list[str]
    hint: str


QUESTIONS: list[Question] = [

    # ================================================================
    # Category: Anime
    # ================================================================

    {
        "category": "Anime 🎌",
        "clue": "His body is rubber, he always laughs, dreams of becoming the Pirate King, and wears a straw hat.",
        "answers": ["Luffy", "لوفي", "Monkey D Luffy", "monkey d luffy"],
        "hint": "Anime: One Piece",
    },
    {
        "category": "Anime 🎌",
        "clue": "Carries three swords, sleeps often, hates losing, and wants to become the greatest swordsman.",
        "answers": ["Zoro", "زورو", "Roronoa Zoro", "roronoa zoro"],
        "hint": "Anime: One Piece",
    },
    {
        "category": "Anime 🎌",
        "clue": "A ninja carrying the Nine-Tailed Fox, loves ramen, and his dream is to become Hokage.",
        "answers": ["Naruto", "ناروتو", "Naruto Uzumaki", "naruto uzumaki"],
        "hint": "Anime: Naruto",
    },
    {
        "category": "Anime 🎌",
        "clue": "Bald head, wears a yellow suit, kills any enemy with just one punch.",
        "answers": ["Saitama", "سايتاما", "One Punch Man", "one punch man"],
        "hint": "Anime: One Punch Man",
    },
    {
        "category": "Anime 🎌",
        "clue": "Fights demons to free his sister, learns Breathing styles, has decorated ears, and a red sword.",
        "answers": ["Tanjiro", "تانجيرو", "Tanjiro Kamado", "tanjirou", "tanjiro kamado"],
        "hint": "Anime: Demon Slayer",
    },
    {
        "category": "Anime 🎌",
        "clue": "Smartest person in the world, sits in a strange way, always eats sweets, and fights crime.",
        "answers": ["L", "إل", "ل", "lawliet", "l lawliet"],
        "hint": "Anime: Death Note",
    },
    {
        "category": "Anime 🎌",
        "clue": "A high school student who finds a notebook that kills anyone whose name is written in it, wanting to become a god.",
        "answers": ["Light", "لايتو", "لايت", "light yagami", "لايت ياغامي", "Yagami"],
        "hint": "Anime: Death Note",
    },
    {
        "category": "Anime 🎌",
        "clue": "Sees spirits, carries a soul sword, fights hollows, and has orange hair.",
        "answers": ["Ichigo", "إيشيغو", "Ichigo Kurosaki", "kurosaki ichigo", "ايشيغو"],
        "hint": "Anime: Bleach",
    },
    {
        "category": "Anime 🎌",
        "clue": "Plans to destroy all titans, became a titan himself, his mother was eaten by a titan.",
        "answers": ["Eren", "إيرين", "Eren Yeager", "eren yeager", "ايرين"],
        "hint": "Anime: Attack on Titan",
    },
    {
        "category": "Anime 🎌",
        "clue": "His goal is to find his father, uses Nen, his friend walks with a huge needle, has blonde hair and green eyes.",
        "answers": ["Gon", "غون", "Gon Freecss", "gon freecs", "جون"],
        "hint": "Anime: Hunter x Hunter",
    },
    {
        "category": "Anime 🎌",
        "clue": "A genius boy, small body but big brain, searching for the Philosopher's Stone with his brother.",
        "answers": ["Edward", "إدوارد", "Edward Elric", "edward elric", "Ed"],
        "hint": "Anime: Fullmetal Alchemist",
    },
    {
        "category": "Anime 🎌",
        "clue": "Wears a triangle-patterned haori, falls asleep to fight, and has yellow hair.",
        "answers": ["Zenitsu", "زنيتسو", "Zenitsu Agatsuma", "zenitsu agatsuma"],
        "hint": "Hint: He sleeps to fight in Demon Slayer",
    },
    {
        "category": "Anime 🎌",
        "clue": "A cyborg hero, Saitama's disciple, has silver hair and black eyes with yellow irises.",
        "answers": ["Genos", "جينوس", "Genos", "genos"],
        "hint": "Anime: One Punch Man",
    },
    {
        "category": "Anime 🎌",
        "clue": "A beautiful girl, white hair, can transform into a demon, one of Fairy Tail's most famous characters.",
        "answers": ["Mirajane", "ميراجين", "Mira", "mira", "mirajane strauss"],
        "hint": "Anime: Fairy Tail",
    },
    {
        "category": "Anime 🎌",
        "clue": "Learned Sun Breathing, son of a charcoal seller, has a scar on his forehead.",
        "answers": ["Tanjiro", "تانجيرو"],
        "hint": "Second hint: Demon Slayer",
    },

    # ================================================================
    # Category: Cars
    # ================================================================

    {
        "category": "Cars 🚗",
        "clue": "Luxury Italian car, logo is a yellow prancing horse, most famous color is red, eternal rival of Lamborghini.",
        "answers": ["Ferrari", "فيراري", "ferrari"],
        "hint": "From Italy",
    },
    {
        "category": "Cars 🚗",
        "clue": "Italian car, logo is a raging bull, insane speed, always competing with Ferrari.",
        "answers": ["Lamborghini", "لامبورغيني", "lamborghini", "Lambo"],
        "hint": "Also from Italy",
    },
    {
        "category": "Cars 🚗",
        "clue": "German luxury car, logo is a three-pointed star inside a circle, known for luxury and safety.",
        "answers": ["Mercedes", "مرسيدس", "mercedes benz", "Mercedes Benz"],
        "hint": "Germany",
    },
    {
        "category": "Cars 🚗",
        "clue": "German car, logo is four interlocking rings, manufactured in Ingolstadt.",
        "answers": ["Audi", "أودي", "audi"],
        "hint": "Germany",
    },
    {
        "category": "Cars 🚗",
        "clue": "Ultra-luxury British car, made for kings and heads of state, has a Spirit of Ecstasy statue on the hood.",
        "answers": ["Rolls Royce", "رولز رويس", "rolls royce", "Rolls"],
        "hint": "Britain",
    },
    {
        "category": "Cars 🚗",
        "clue": "Legendary American sports car, named after a wild horse, manufactured by Ford since 1964.",
        "answers": ["Mustang", "موستانج", "mustang", "Ford Mustang", "ford mustang"],
        "hint": "America — Ford",
    },
    {
        "category": "Cars 🚗",
        "clue": "Electric car, its owner is one of the richest people in the world, models S, 3, X, and Y.",
        "answers": ["Tesla", "تسلا", "tesla"],
        "hint": "Runs on electricity",
    },
    {
        "category": "Cars 🚗",
        "clue": "Japanese sports car, nicknamed GTR or Skyline, was the fastest production car in the nineties.",
        "answers": ["Nissan GTR", "GTR", "gtr", "Nissan", "nissan gtr", "Skyline", "skyline"],
        "hint": "Japan — Nissan",
    },
    {
        "category": "Cars 🚗",
        "clue": "Swedish car, famous for being the safest car in the world, logo is a circle with an arrow pointing out.",
        "answers": ["Volvo", "فولفو", "volvo"],
        "hint": "Sweden",
    },
    {
        "category": "Cars 🚗",
        "clue": "Japanese car company, its name means 'abundant rice field', the best-selling in the Gulf for many years.",
        "answers": ["Toyota", "تويوتا", "toyota"],
        "hint": "Japan — Toyota",
    },
    {
        "category": "Cars 🚗",
        "clue": "German sports car, logo features the coat of arms of the Free People's State of Württemberg, known for 911 and 718 models.",
        "answers": ["Porsche", "بورش", "porsche"],
        "hint": "Germany",
    },
    {
        "category": "Cars 🚗",
        "clue": "American luxury brand, famous for large SUVs and sedans, owned by GM.",
        "answers": ["Cadillac", "كاديلاك", "cadillac"],
        "hint": "America — GM",
    },
    {
        "category": "Cars 🚗",
        "clue": "French car company, logo features a silver lion.",
        "answers": ["Peugeot", "بيجو", "peugeot"],
        "hint": "France",
    },
    {
        "category": "Cars 🚗",
        "clue": "South Korean car brand that has become one of the best in quality, logo is a slanted H.",
        "answers": ["Hyundai", "هيونداي", "hyundai"],
        "hint": "South Korea",
    },
    {
        "category": "Cars 🚗",
        "clue": "American pickup truck, the best-selling in America for over 40 consecutive years.",
        "answers": ["Ford F150", "F-150", "F150", "ford f150", "Ford"],
        "hint": "America — Ford",
    },
]
