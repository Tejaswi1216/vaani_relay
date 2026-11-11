import pytest
from relay.verifier import verify_answer, extract_citations, extract_quotes

def test_extract_citations():
    text = "The Transformer uses 8 heads [attention_v7, page 4] and feed-forward networks [attention_v7, page 5, section 3.3]."
    cits = extract_citations(text)
    assert len(cits) == 2
    assert cits[0].paper == "attention_v7"
    assert cits[0].page == 4
    assert cits[1].paper == "attention_v7"
    assert cits[1].page == 5
    assert cits[1].section == "3.3"

def test_valid_quote_and_citation():
    # Page 5 of attention_v7 contains: "inner-layer has dimensionality" or "d_ff = 2048" or "linear transformations"
    answer = 'The FFN consists of "two linear transformations" with a ReLU activation [attention_v7, page 5].'
    res = verify_answer(answer)
    assert res.valid is True
    assert len(res.verified_quotes) == 1
    assert len(res.missing_quotes) == 0

def test_hallucinated_quote_fails():
    answer = 'The Transformer uses "super-quantum multi-dimensional warp drive" [attention_v7, page 5].'
    res = verify_answer(answer)
    assert res.valid is False
    assert len(res.missing_quotes) == 1
    assert "Quoted span not found" in res.errors[0]

def test_wrong_page_fails():
    # "two linear transformations" is on page 5, not page 1
    answer = 'The model uses "two linear transformations" [attention_v7, page 1].'
    res = verify_answer(answer)
    assert res.valid is False

def test_invalid_paper_fails():
    answer = 'Some claim "two linear transformations" [unknown_paper_v1, page 5].'
    res = verify_answer(answer)
    assert res.valid is False
    assert "Invalid paper cited" in res.errors[0]

def test_refusal_is_valid():
    answer = "The papers in the library do not contain information to answer this question."
    res = verify_answer(answer)
    assert res.valid is True
