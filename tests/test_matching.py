from datetime import date, timedelta

from app.geo import Point, estimate_minutes, haversine_km
from app.matching import (
    Fit,
    ListingFacts,
    MatchConfig,
    StudentQuery,
    Tier,
    TravelRuleConfig,
    check_age,
    check_dates,
    check_grade,
    domain_score,
    evaluate,
    rank,
)

TODAY = date(2026, 10, 17)
FRANKFURT = Point(50.1109, 8.6821)
OFFENBACH = Point(50.1060, 8.7650)
DARMSTADT = Point(49.8728, 8.6512)


def listing(**kw):
    base = dict(id=1, domain_ids={1}, direct_domain_ids={1}, min_age=14, max_age=17, min_grade=8, max_grade=10,
                windows=[(date(2026, 11, 1), date(2027, 2, 28))], deadline=date(2026, 12, 1),
                availability="open", verification_status="admin_verified",
                last_verified_at=TODAY - timedelta(days=5), point=OFFENBACH)
    base.update(kw)
    return ListingFacts(**base)


def query(**kw):
    base = dict(today=TODAY, domain_ids={1}, age=15, grade=9, start=date(2026, 11, 9), end=date(2026, 11, 20),
                school=FRANKFURT, max_minutes=45, mode="oepnv")
    base.update(kw)
    return StudentQuery(**base)


def test_strong_match_when_everything_fits():
    r = evaluate(query(), listing())
    assert r.tier == Tier.STRONG
    assert r.primary_label.key == "strong"
    assert "Alter" in r.primary_label.explanation or "dein Alter" in r.primary_label.explanation


def test_too_young_is_not_eligible_with_reason():
    r = evaluate(query(age=13), listing())
    assert r.tier == Tier.NOT_ELIGIBLE
    assert "mindestens 14" in r.primary_label.explanation


def test_age_boundaries_inclusive():
    assert check_age(query(age=14), listing())[0] == Fit.FIT
    assert check_age(query(age=17), listing())[0] == Fit.FIT
    assert check_age(query(age=18), listing())[0] == Fit.NO_FIT


def test_missing_age_is_unknown_not_fit():
    fit, reason = check_age(query(), listing(min_age=None, max_age=None))
    assert fit == Fit.UNKNOWN
    assert "nachfragen" in reason
    assert evaluate(query(), listing(min_age=None, max_age=None)).tier == Tier.POSSIBLE


def test_grade_check():
    assert check_grade(query(grade=7), listing())[0] == Fit.NO_FIT
    assert check_grade(query(grade=None), listing())[0] == Fit.NOT_ASKED


def test_dates_covered_partial_and_outside():
    l = listing(windows=[(date(2026, 11, 1), date(2026, 11, 15))])
    assert check_dates(query(), l)[0] == Fit.PARTIAL
    l2 = listing(windows=[(date(2027, 3, 1), date(2027, 3, 31))])
    assert check_dates(query(), l2)[0] == Fit.NO_FIT
    assert evaluate(query(), l2).tier == Tier.NOT_ELIGIBLE


def test_flexible_dates_are_possible_not_rejected():
    l = listing(windows=[(date(2027, 3, 1), date(2027, 3, 31))], flexible_dates=True)
    assert check_dates(query(), l)[0] == Fit.UNKNOWN
    assert evaluate(query(), l).tier == Tier.POSSIBLE


def test_distant_listing_requires_school_approval_but_is_not_rejected():
    r = evaluate(query(max_minutes=30), listing(point=DARMSTADT))
    assert r.tier == Tier.POSSIBLE
    keys = [l.key for l in r.labels]
    assert "school_approval" in keys
    assert r.travel.minutes > 30


def test_home_location_used_when_closer():
    r = evaluate(query(school=DARMSTADT, home=OFFENBACH), listing(point=OFFENBACH))
    assert r.travel.reference == "home"


def test_rule_school_only_ignores_home():
    cfg = MatchConfig(travel_rule=TravelRuleConfig(reference="school", max_minutes=45))
    r = evaluate(query(school=DARMSTADT, home=OFFENBACH), listing(point=OFFENBACH), cfg)
    assert r.travel.reference == "school"


def test_deadline_passed_and_approaching():
    r = evaluate(query(), listing(deadline=TODAY - timedelta(days=1)))
    assert r.tier == Tier.NOT_ELIGIBLE
    assert "deadline_passed" in [l.key for l in r.labels]
    r2 = evaluate(query(), listing(deadline=TODAY + timedelta(days=5)))
    assert "deadline" in [l.key for l in r2.labels]


def test_unverified_or_stale_never_strong():
    assert evaluate(query(), listing(verification_status="unverified")).tier == Tier.POSSIBLE
    stale = listing(last_verified_at=TODAY - timedelta(days=200))
    r = evaluate(query(), stale)
    assert r.tier == Tier.POSSIBLE
    assert "stale" in [l.key for l in r.labels]


def test_closed_and_expired():
    assert evaluate(query(), listing(availability="full")).tier == Tier.NOT_ELIGIBLE
    r = evaluate(query(), listing(expires_at=TODAY - timedelta(days=1)))
    assert "expired" in [l.key for l in r.labels]


def test_domain_score_parent_child():
    # student picks parent 1; listing tagged with subdomain 5 (parent 1)
    l = listing(domain_ids={1, 5}, direct_domain_ids={5})
    assert domain_score(query(domain_ids={1}), l) == 2
    # student picks subdomain 5; listing only in parent 1 -> related
    l2 = listing(domain_ids={1}, direct_domain_ids={1})
    assert domain_score(query(domain_ids={5}, related_domain_ids={1}), l2) == 1
    assert domain_score(query(domain_ids={9}), l2) == 0


def test_ranking_order_eligibility_then_dates_then_travel():
    near_partial = listing(id=1, windows=[(date(2026, 11, 1), date(2026, 11, 12))])
    far_fit = listing(id=2, point=DARMSTADT)
    near_fit = listing(id=3)
    too_old = listing(id=4, max_age=14)
    order = [r.listing_id for r in rank(query(max_minutes=90), [too_old, near_partial, far_fit, near_fit])]
    # strong first; among "possible", full date fit beats partial dates even if farther; ineligible last
    assert order == [3, 2, 1, 4]


def test_no_inputs_gives_possible_not_strong():
    r = evaluate(StudentQuery(today=TODAY), listing())
    assert r.tier == Tier.POSSIBLE


def test_geo_estimates_are_reasonable():
    km = haversine_km(FRANKFURT, DARMSTADT)
    assert 25 < km < 28
    assert 60 < estimate_minutes(km, "oepnv") < 120
    assert estimate_minutes(km, "auto") < estimate_minutes(km, "oepnv")
