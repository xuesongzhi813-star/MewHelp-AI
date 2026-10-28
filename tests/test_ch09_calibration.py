from dataclasses import replace
import pytest
from tests.test_ch09_evidence import retrieval


def examples():
    from app.kb.calibration import CalibrationCase
    cases = [CalibrationCase(f'c{i:02}', 'calibration', i<20, frozenset({1}) if i<20 else frozenset())
             for i in range(24)]
    results = {case.id:retrieval([.9 if case.answerable else .1]) for case in cases}
    return cases, results


def test_calibration_grid_selects_reproducibly_using_only_calibration_inputs():
    from app.kb.calibration import calibrate
    cases, results = examples()
    first = calibrate(cases, results, identity={'corpus_hash':'synthetic'})
    results['held-out'] = retrieval([1.])
    second = calibrate(cases, results, identity={'corpus_hash':'synthetic'})
    assert (first.cutoff,first.weights,first.threshold,first.calibration_hash) == (
        second.cutoff,second.weights,second.threshold,second.calibration_hash)
    assert first.weights == (.8,.1,.1)
    assert first.cutoff == .1
    assert first.threshold == pytest.approx((.8*.1+.1/3+.8*.9+.1/3)/2)
    assert first.diagnostics['selected']['false_positive'] == 0
    assert first.diagnostics['selected']['false_negative'] == 0
    assert len(first.diagnostics['samples']) == 24


def test_test_split_is_rejected_before_any_threshold_selection():
    from app.kb.calibration import calibrate
    cases, results = examples()
    with pytest.raises(ValueError,match='calibration'):
        calibrate([replace(cases[0],split='test'),*cases[1:]],results,identity={})


def test_missing_label_class_or_incomplete_results_prevents_calibration():
    from app.kb.calibration import calibrate
    cases, results = examples()
    with pytest.raises(ValueError):
        calibrate([replace(c,answerable=True) for c in cases],results,identity={})
    results.pop(cases[0].id)
    with pytest.raises(ValueError):
        calibrate(cases,results,identity={})


def test_invalid_provider_data_cannot_be_used_as_negative_evidence():
    from app.kb.calibration import calibrate
    cases, results = examples()
    results[cases[0].id] = retrieval([float('nan')])
    with pytest.raises(ValueError):
        calibrate(cases,results,identity={})
