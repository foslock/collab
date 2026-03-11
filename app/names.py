"""Random name generator: <adjective> <animal>."""

ADJECTIVES = [
    "Brave", "Clever", "Daring", "Eager", "Fierce", "Gentle", "Happy",
    "Jolly", "Kind", "Lively", "Mighty", "Noble", "Proud", "Quick",
    "Radiant", "Silent", "Swift", "Tender", "Vivid", "Warm", "Witty",
    "Zesty", "Bold", "Calm", "Dizzy", "Fancy", "Giddy", "Hasty",
    "Icy", "Jazzy", "Keen", "Loud", "Merry", "Neat", "Odd", "Peppy",
    "Quirky", "Rusty", "Silly", "Tiny", "Unique", "Vast", "Wild",
    "Xenial", "Young", "Zippy", "Agile", "Breezy", "Crisp", "Dapper",
]

ANIMALS = [
    "Alpaca", "Bear", "Cat", "Dolphin", "Eagle", "Fox", "Goose",
    "Hedgehog", "Iguana", "Jaguar", "Koala", "Lemur", "Moose",
    "Narwhal", "Otter", "Penguin", "Quail", "Raccoon", "Seal",
    "Tiger", "Urchin", "Vulture", "Walrus", "Yak", "Zebra",
    "Axolotl", "Bison", "Crane", "Dove", "Elk", "Falcon", "Gecko",
    "Heron", "Ibis", "Jay", "Kiwi", "Lynx", "Mink", "Newt",
    "Owl", "Panda", "Robin", "Sloth", "Toad", "Viper", "Wolf",
    "Wren", "Finch", "Hawk", "Lark",
]

# Vivid, distinguishable colors for drawing
COLORS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9A6324", "#800000", "#aaffc3", "#808000",
    "#000075", "#a9a9a9", "#e6beff", "#ffe119", "#ffd8b1",
]

import random
from typing import Set


def generate_name(taken_names: Set[str]) -> tuple[str, str]:
    """Generate a unique adjective-animal name and a color.

    Returns (name, color).
    """
    available = [
        f"{adj} {ani}"
        for adj in ADJECTIVES
        for ani in ANIMALS
        if f"{adj} {ani}" not in taken_names
    ]
    if not available:
        # extremely unlikely fallback
        name = f"User-{random.randint(1000, 9999)}"
    else:
        name = random.choice(available)

    color = random.choice(COLORS)
    return name, color
