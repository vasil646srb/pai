import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pai.laya as L
from pai.laya import Laya, JevLaya, laya_settings_path, load_laya_settings, save_laya_settings, laya_verify_pair, Verdict

class St:
    def __init__(self): self.rows = []
    def log_action(self, kind, name, request, **k): self.rows.append((name, k))

def test_settings_roundtrip(tmp_path):
    p = laya_settings_path(tmp_path)
    assert load_laya_settings(p) == {}
    save_laya_settings(p, {"use_jev_verify": True})
    assert load_laya_settings(p) == {"use_jev_verify": True}
    p.write_text("not json"); assert load_laya_settings(p) == {}

def test_acts_verify_rules():
    l = Laya("http://127.0.0.1:1", "shadow")
    assert not l.acts_verify
    l.use_for_verify = True; assert l.acts_verify and not l.acts   # switch acts for verify only
    l.use_for_verify = False; l.mode = "on"; assert l.acts_verify and l.acts
    off = Laya("", "off"); off.use_for_verify = True; assert not off.acts_verify

def test_pair_logs_agreement_and_skips_laya_skip():
    st = St()
    jev = {"answers": Verdict("answers", {"answers": 0.9, "partial_or_off_topic": 0.1}, 0.5),
           "language": Verdict("same_language", {"same_language": 0.9, "different_language": 0.1}, 0.5)}
    laya_verify_pair(st, "s", jev, {"status": "pass", "usefulness": 9}, 3)
    name, k = st.rows[-1]
    assert name == "verify_pair" and k["response"]["actual"]["agree"] is True
    laya_verify_pair(st, "s", jev, {"status": "fail"}, 3)
    assert st.rows[-1][1]["response"]["actual"]["agree"] is False
    n = len(st.rows)
    laya_verify_pair(st, "s", jev, {"status": "pass", "skipped_by": "laya"}, 3)
    assert len(st.rows) == n   # решение Jev, не LLM: пары нет
