from app.services.hash_utils import stable_json_hash

def test_stable_json_hash_order_independent():
    assert stable_json_hash({"b": 2, "a": 1}) == stable_json_hash({"a": 1, "b": 2})
