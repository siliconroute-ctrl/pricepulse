from pricepulse.basket import optimise


def test_single_store_needs_full_coverage_and_split_shop():
    prices = {1: {"A": 1000, "B": 900}, 2: {"A": 500, "B": 700}, 3: {"A": 200}}
    r = optimise({1: 2, 2: 1, 3: 1}, prices)
    assert r.single_store.store == "A" and r.single_store.total_cents == 2700
    b = next(t for t in r.stores if t.store == "B")
    assert not b.full_coverage and b.missing == [3]
    assert r.split_total_cents == 900 * 2 + 500 + 200
    assert r.split_saving_cents == 200
    assert {(line.product_id, line.store) for line in r.split} == {(1, "B"), (2, "A"), (3, "A")}


def test_no_store_has_everything():
    r = optimise({1: 1, 2: 1}, {1: {"A": 100}, 2: {"B": 100}})
    assert r.single_store is None and r.split_total_cents == 200 and r.split_saving_cents == 0


def test_unknown_items_and_zero_quantity():
    r = optimise({1: 1, 99: 3, 2: 0}, {1: {"A": 100}})
    assert r.not_found == [99] and r.split_total_cents == 100


def test_ties_are_stable():
    r = optimise({1: 1}, {1: {"B": 100, "A": 100}})
    assert r.single_store.store == "A" and r.split[0].store == "A"
