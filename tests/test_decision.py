from scorer.decision import (
    END,
    WARN,
    DecisionConfig,
    DecisionEngine,
    combine_risk,
    is_allowlisted,
    normalize_phone,
)


def engine(**overrides) -> DecisionEngine:
    return DecisionEngine(DecisionConfig(**overrides))


def kinds(actions) -> list[str]:
    return [action.kind for action in actions]


def test_risk_is_the_higher_of_the_two_scorers():
    assert combine_risk(40, 70) == 70
    assert combine_risk(70, 40) == 70
    assert combine_risk(55, None) == 55


def test_below_warn_threshold_never_acts():
    e = engine()
    for t in range(20, 60, 5):
        assert e.observe(t, rule_risk=59, llm_risk=59) == []


def test_warn_needs_two_consecutive_evaluations():
    e = engine()
    assert e.observe(20, 0, 60) == []
    assert kinds(e.observe(23, 0, 60)) == [WARN]


def test_threshold_is_inclusive():
    e = engine()
    e.observe(20, 0, 85)
    assert kinds(e.observe(23, 0, 85)) == [END]


def test_streak_resets_when_risk_drops():
    e = engine()
    assert e.observe(20, 0, 70) == []
    assert e.observe(23, 0, 30) == []  # one spike, then back to normal
    assert e.observe(26, 0, 70) == []
    assert kinds(e.observe(29, 0, 70)) == [WARN]


def test_end_needs_two_consecutive_evaluations_at_its_own_threshold():
    e = engine()
    assert e.observe(20, 0, 70) == []
    assert kinds(e.observe(23, 0, 90)) == [WARN]  # second evaluation over 60, first over 85
    assert kinds(e.observe(26, 0, 90)) == [END]


def test_end_at_the_same_moment_replaces_warn():
    e = engine()
    assert e.observe(20, 0, 95) == []
    assert kinds(e.observe(23, 0, 95)) == [END]
    assert e.observe(26, 0, 95) == []  # and no late WARN


def test_at_most_one_warn_and_one_end_per_call():
    e = engine()
    fired = []
    for t in range(20, 80, 3):
        fired += kinds(e.observe(t, 0, 70))
    for t in range(80, 140, 3):
        fired += kinds(e.observe(t, 0, 95))
    assert fired == [WARN, END]
    assert e.ended


def test_no_action_in_the_first_ten_seconds():
    e = engine()
    assert e.observe(2, 0, 95) == []
    assert e.observe(5, 0, 95) == []
    assert e.observe(9.9, 0, 95) == []  # streak is long enough, but the call is too young
    assert kinds(e.observe(10, 0, 95)) == [END]


def test_grace_period_does_not_fire_without_a_fresh_evaluation_over_threshold():
    e = engine()
    e.observe(2, 0, 95)
    e.observe(5, 0, 95)
    assert e.observe(12, 0, 20) == []


def test_rules_and_llm_agreeing_confirm_in_one_evaluation():
    e = engine()
    assert kinds(e.observe(20, rule_risk=90, llm_risk=92)) == [END]


def test_agreement_shortcut_can_be_turned_off():
    e = engine(agreement_confirms=False)
    assert e.observe(20, rule_risk=90, llm_risk=92) == []
    assert kinds(e.observe(23, rule_risk=90, llm_risk=92)) == [END]


def test_one_scorer_alone_still_needs_a_second_evaluation():
    e = engine()
    assert e.observe(20, rule_risk=10, llm_risk=92) == []
    assert kinds(e.observe(23, rule_risk=10, llm_risk=92)) == [END]
    e = engine()
    assert e.observe(20, rule_risk=90, llm_risk=None) == []  # LLM timed out: rules only
    assert kinds(e.observe(23, rule_risk=90, llm_risk=None)) == [END]


def test_agreement_is_judged_per_threshold():
    e = engine()
    # Both over 60 (WARN now), only the LLM over 85 (END needs another evaluation).
    assert kinds(e.observe(20, rule_risk=70, llm_risk=90)) == [WARN]
    assert kinds(e.observe(23, rule_risk=70, llm_risk=90)) == [END]


def test_thresholds_are_configurable():
    e = engine(warn_threshold=40, end_threshold=70, consecutive_required=1, grace_period_s=0)
    assert kinds(e.observe(0, 0, 45)) == [WARN]
    assert kinds(e.observe(1, 0, 75)) == [END]


def test_action_reports_when_its_streak_started():
    e = engine()
    e.observe(20, 0, 70)
    (warn,) = e.observe(24, 0, 75)
    assert (warn.kind, warn.risk, warn.t, warn.streak_started_t) == (WARN, 75, 24, 20)


def test_allowlisted_call_is_never_acted_on():
    e = DecisionEngine(DecisionConfig(), monitoring_enabled=False)
    for t in range(20, 60, 3):
        assert e.observe(t, 100, 100) == []


def test_allowlist_matches_across_number_formats():
    allowlist = ["+1 (404) 555-0142", "6785550199"]
    assert is_allowlisted("+14045550142", allowlist)
    assert is_allowlisted("404-555-0142", allowlist)
    assert is_allowlisted("+16785550199", allowlist)
    assert not is_allowlisted("+14045550143", allowlist)
    assert not is_allowlisted("+14045550142", [])


def test_withheld_numbers_are_never_allowlisted():
    assert not is_allowlisted("", ["+14045550142"])
    assert not is_allowlisted("anonymous", ["anonymous", ""])
    assert normalize_phone("+1 (404) 555-0142") == "14045550142"
