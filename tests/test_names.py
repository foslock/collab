"""Tests for the name generator."""

from app.names import generate_name, ADJECTIVES, ANIMALS, COLORS


class TestGenerateName:
    def test_returns_name_and_color(self):
        name, color = generate_name(set())
        assert isinstance(name, str)
        assert isinstance(color, str)
        assert color.startswith("#")

    def test_name_is_adjective_animal_format(self):
        name, _ = generate_name(set())
        parts = name.split(" ")
        assert len(parts) == 2
        assert parts[0] in ADJECTIVES
        assert parts[1] in ANIMALS

    def test_name_not_in_taken_set(self):
        taken = {"Brave Alpaca", "Clever Bear"}
        for _ in range(50):
            name, _ = generate_name(taken)
            assert name not in taken

    def test_color_is_from_colors_list(self):
        _, color = generate_name(set())
        assert color in COLORS

    def test_all_names_taken_falls_back(self):
        all_names = {f"{adj} {ani}" for adj in ADJECTIVES for ani in ANIMALS}
        name, color = generate_name(all_names)
        assert name.startswith("User-")
        assert color in COLORS

    def test_generates_unique_names(self):
        taken = set()
        for _ in range(20):
            name, _ = generate_name(taken)
            assert name not in taken
            taken.add(name)
        assert len(taken) == 20

    def test_total_combinations(self):
        total = len(ADJECTIVES) * len(ANIMALS)
        assert total >= 2000  # should be 2500
