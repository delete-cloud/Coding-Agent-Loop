from coding_agent.cli.input_handler import expand_pasted_refs, fold_pasted_content


class TestFoldPastedContent:
    def test_short_text_unchanged(self):
        text = "hello world"
        folded, refs = fold_pasted_content(text, threshold=20)
        assert folded == text
        assert refs == {}

    def test_long_text_folded(self):
        text = "\n".join(f"line {i}" for i in range(25))
        folded, refs = fold_pasted_content(text, threshold=20)
        assert "[Pasted text" in folded
        assert len(refs) == 1

    def test_folded_text_contains_line_count(self):
        lines = [f"line {i}" for i in range(30)]
        text = "\n".join(lines)
        folded, refs = fold_pasted_content(text, threshold=20)
        assert "+30 lines" in folded or "30 lines" in folded

    def test_refs_contain_original_text(self):
        lines = [f"line {i}" for i in range(25)]
        text = "\n".join(lines)
        folded, refs = fold_pasted_content(text, threshold=20)
        ref_id = next(iter(refs))
        assert refs[ref_id] == text

    def test_exactly_threshold_not_folded(self):
        text = "\n".join(f"x" for _ in range(20))
        folded, refs = fold_pasted_content(text, threshold=20)
        assert folded == text
        assert refs == {}

    def test_one_over_threshold_folded(self):
        text = "\n".join(f"x" for _ in range(21))
        folded, refs = fold_pasted_content(text, threshold=20)
        assert "[Pasted text" in folded

    def test_placeholder_is_single_line(self):
        text = "\n".join(f"line {i}" for i in range(25))
        folded, refs = fold_pasted_content(text, threshold=20)
        assert "\n" not in folded

    def test_long_text_above_threshold_single_ref(self):
        text1 = "\n".join(f"a{i}" for i in range(25))
        text2 = "\n".join(f"b{i}" for i in range(25))
        combined = f"before\n{text1}\nmiddle\n{text2}\nafter"
        folded, refs = fold_pasted_content(combined, threshold=20)
        assert len(refs) == 1
        assert "[Pasted text" in folded

    def test_preserves_context_around_long_block(self):
        block = "\n".join(f"line {i}" for i in range(25))
        text = f"before context\n\n{block}\n\nafter context"
        folded, refs = fold_pasted_content(text, threshold=20)
        assert "before context" not in folded
        assert "after context" not in folded
        assert "[Pasted text" in folded
        assert len(refs) == 1
        assert expand_pasted_refs(folded, refs) == text

    def test_paste_with_blank_lines_folds_as_single_block(self):
        blocks = [
            "\n".join(f"line {i}" for i in range(start, start + 15))
            for start in range(0, 105, 15)
        ]
        text = "\n\n".join(blocks)

        folded, refs = fold_pasted_content(text, threshold=20, ref_id="1")

        assert "[Pasted text #1" in folded
        assert len(refs) == 1
        assert refs == {"1": text}

    def test_sequential_ref_id_in_placeholder(self):
        text = "\n".join(f"line {i}" for i in range(25))

        folded, refs = fold_pasted_content(text, threshold=20, ref_id="1")

        assert "#1" in folded
        assert refs == {"1": text}

    def test_sequential_ref_id_round_trips(self):
        text = "\n".join(f"line {i}" for i in range(50))

        folded, refs = fold_pasted_content(text, threshold=20, ref_id="42")

        assert expand_pasted_refs(folded, refs) == text

    def test_no_uuid_hex_in_ref_keys(self):
        text = "\n".join(f"line {i}" for i in range(25))

        _, refs = fold_pasted_content(text, threshold=20, ref_id="7")

        assert set(refs) == {"7"}

    def test_long_single_line_is_folded(self):
        text = "x" * 5000
        folded, refs = fold_pasted_content(text, threshold=20)
        assert "[Pasted text" in folded
        assert len(refs) == 1


class TestExpandPastedRefs:
    def test_expand_restores_original(self):
        lines = [f"line {i}" for i in range(25)]
        text = "\n".join(lines)
        folded, refs = fold_pasted_content(text, threshold=20)
        expanded = expand_pasted_refs(folded, refs)
        assert expanded == text

    def test_expand_no_refs_unchanged(self):
        text = "hello world"
        expanded = expand_pasted_refs(text, {})
        assert expanded == text

    def test_expand_with_surrounding_text(self):
        lines = [f"line {i}" for i in range(25)]
        paste = "\n".join(lines)
        text = f"Please look at this:\n{paste}\nAnd fix it."
        folded, refs = fold_pasted_content(text, threshold=20)
        expanded = expand_pasted_refs(folded, refs)
        assert expanded == text

    def test_expand_preserves_surrounding_context_for_folded_block(self):
        block = "\n".join(f"line {i}" for i in range(25))
        text = f"before context\n\n{block}\n\nafter context"
        folded, refs = fold_pasted_content(text, threshold=20)
        expanded = expand_pasted_refs(folded, refs)
        assert expanded == text

    def test_expand_empty_refs(self):
        result = expand_pasted_refs("some text [Pasted text #1 +5 lines]", {})
        assert result == "some text [Pasted text #1 +5 lines]"
