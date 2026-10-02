from evaluation.replay import run
def test_replay_math():
    r = run(["Yes"] * 100 + ["No"] * 100, 500, 1.0, 1.0, 1, "synthetic replay")
    assert r["refilled"] == 100 and r["recovered_revenue"] == 50000 and r["refill_rate"] == 1.0
    assert run(["No"] * 10, 500, 1, 1, 1, "x")["refilled"] == 0
