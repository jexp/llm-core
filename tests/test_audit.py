from kernel.audit import GENESIS, InMemoryAuditSink, verify_chain


def test_chain_appends_and_verifies():
    sink = InMemoryAuditSink()
    for i in range(3):
        sink.append("RECEIVED", f"req-{i}", "tester", {"i": i})
    assert len(sink.all()) == 3
    assert sink.all()[0].prev_hash == GENESIS
    assert verify_chain(sink.all())["ok"] is True


def test_chain_detects_tampering():
    sink = InMemoryAuditSink()
    sink.append("RECEIVED", "r1", "tester", {"a": 1})
    sink.append("DECIDED", "r1", "tester", {"tier": "t0"})
    records = sink.all()
    records[0].data["a"] = 999  # tamper
    report = verify_chain(records)
    assert report["ok"] is False
    assert report["first_bad_seq"] == 1


def test_chain_detects_deletion():
    sink = InMemoryAuditSink()
    for i in range(4):
        sink.append("E", f"r{i}", "tester", {"i": i})
    records = sink.all()
    del records[1]  # delete a middle entry
    report = verify_chain(records)
    assert report["ok"] is False
    assert "sequence gap" in report["reason"]
