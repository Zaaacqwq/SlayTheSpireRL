from sts2rl.seeds import seed_hash, split_seed


def test_split_is_deterministic_and_named():
    assert split_seed("abc") == split_seed("abc")
    assert split_seed("abc") in {"train", "development", "test"}


def test_seed_hash_is_order_independent():
    assert seed_hash(["a", "b"]) == seed_hash(["b", "a"])
