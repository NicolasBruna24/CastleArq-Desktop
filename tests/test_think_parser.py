from castlearq_desktop.core.think_parser import ThinkSplit, split_think_output


def test_normal_text_without_delimiters():
    result = split_think_output("Hola mundo")
    assert result == ThinkSplit(
        raw_output="Hola mundo", final_text="Hola mundo", reasoning_text=""
    )
    assert result.has_reasoning is False


def test_single_complete_block():
    result = split_think_output("<think>razonamiento</think>")
    assert result == ThinkSplit(
        raw_output="<think>razonamiento</think>",
        final_text="",
        reasoning_text="razonamiento",
    )
    assert result.has_reasoning is True


def test_multiple_complete_blocks():
    result = split_think_output("<think>r1</think><think>r2</think>")
    assert result == ThinkSplit(
        raw_output="<think>r1</think><think>r2</think>",
        final_text="",
        reasoning_text="r1\n\nr2",
    )
    assert result.has_reasoning is True


def test_text_before_and_after_blocks():
    result = split_think_output("prefijo <think>r</think> sufijo")
    assert result == ThinkSplit(
        raw_output="prefijo <think>r</think> sufijo",
        final_text="prefijo  sufijo",
        reasoning_text="r",
    )
    assert result.has_reasoning is True


def test_mixed_text_and_multiple_blocks():
    result = split_think_output("a <think>r1</think> b <think>r2</think> c")
    assert result == ThinkSplit(
        raw_output="a <think>r1</think> b <think>r2</think> c",
        final_text="a  b  c",
        reasoning_text="r1\n\nr2",
    )


def test_empty_block_content():
    result = split_think_output("<think></think>")
    assert result == ThinkSplit(
        raw_output="<think></think>", final_text="", reasoning_text=""
    )
    assert result.has_reasoning is False


def test_whitespace_only_block_content():
    result = split_think_output("<think>   \n  </think>")
    assert result == ThinkSplit(
        raw_output="<think>   \n  </think>",
        final_text="",
        reasoning_text="   \n  ",
    )
    assert result.has_reasoning is False


def test_empty_input():
    result = split_think_output("")
    assert result == ThinkSplit(raw_output="", final_text="", reasoning_text="")
    assert result.has_reasoning is False


def test_none_input():
    result = split_think_output(None)
    assert result == ThinkSplit(raw_output="", final_text="", reasoning_text="")
    assert result.has_reasoning is False


def test_non_text_input_int():
    result = split_think_output(123)
    assert result == ThinkSplit(raw_output="123", final_text="123", reasoning_text="")
    assert result.has_reasoning is False


def test_open_without_close_is_conservative():
    text = "<think>sin cierre"
    result = split_think_output(text)
    assert result == ThinkSplit(raw_output=text, final_text=text, reasoning_text="")
    assert result.has_reasoning is False


def test_close_without_open_is_conservative():
    text = "sin apertura</think>"
    result = split_think_output(text)
    assert result == ThinkSplit(raw_output=text, final_text=text, reasoning_text="")
    assert result.has_reasoning is False


def test_unbalanced_delimiters_are_conservative():
    text = "<think>a</think> <think>b"
    result = split_think_output(text)
    assert result == ThinkSplit(raw_output=text, final_text=text, reasoning_text="")
    assert result.has_reasoning is False


def test_has_reasoning_true_and_false():
    assert split_think_output("<think>x</think>").has_reasoning is True
    assert split_think_output("sin thinking").has_reasoning is False
    assert split_think_output("<think>   </think>").has_reasoning is False
