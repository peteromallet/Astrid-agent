"""Small, model-agnostic semantic checkers for timeline evaluation artifacts.

The visible task brief and this module's hidden expectations are deliberately
separate.  Check functions consume plain JSON-shaped mappings so the evaluator
does not need Astrid Runtime or a particular agent adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

Json = Mapping[str, Any]


# These values are intentionally a small policy vocabulary, rather than a
# second grading framework.  The suite's assertion table says what evidence a
# case needs; the existing concrete checkers below decide whether that evidence
# matches.  Keeping the vocabulary here makes malformed/ambiguous policy data
# fail at setup instead of silently weakening a later review.
ASSERTION_OUTPUT_POLICIES = frozenset({
    "none",
    "baseline-only",
    "requested-preview",
    "required-after-edit",
    "required-audio",
    "semantic-only",
})
_ASSERTION_POLICY_FIELDS = frozenset({"target", "preserve", "output", "positive", "negative"})
_OUTPUTS_REQUIRING_SAMPLES = frozenset(ASSERTION_OUTPUT_POLICIES - {"none"})
_OUTPUTS_REQUIRING_TOLERANCE = frozenset({
    "required-after-edit", "required-audio", "semantic-only",
})


class AssertionPolicyError(ValueError):
    """The suite's case assertion contract is malformed or incomplete."""


def _non_empty_string_list(value: Any, field: str, case_id: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AssertionPolicyError(f"{case_id} assertion policy {field} must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise AssertionPolicyError(f"{case_id} assertion policy {field} must contain non-empty strings")
    return [str(item) for item in value]


def validate_assertion_policy(
    policy: Mapping[str, Any], *, case_id: str = "<unknown>",
) -> dict[str, Any]:
    """Validate one compact case-to-evidence policy and return a copy.

    This is deliberately not a generic outcome grader.  It only validates the
    contract consumed by the existing checkers: target/preservation scope,
    positive and negative expectations, and the output evidence required for
    the case.  Callers can therefore fail closed before launching a worker,
    while qualitative claims still remain an explicit manual/undetermined
    review rather than an automatic pass.
    """
    if not isinstance(policy, Mapping):
        raise AssertionPolicyError(f"{case_id} assertion policy must be an object")
    missing = sorted(_ASSERTION_POLICY_FIELDS - set(policy))
    if missing:
        raise AssertionPolicyError(
            f"{case_id} assertion policy is missing: {', '.join(missing)}"
        )
    target = policy.get("target")
    if not isinstance(target, str) or not target.strip():
        raise AssertionPolicyError(f"{case_id} assertion policy target must be a non-empty string")
    output = policy.get("output")
    if output not in ASSERTION_OUTPUT_POLICIES:
        allowed = ", ".join(sorted(ASSERTION_OUTPUT_POLICIES))
        raise AssertionPolicyError(
            f"{case_id} assertion policy output must be one of: {allowed}"
        )

    normalized = dict(policy)
    normalized["target"] = target.strip()
    normalized["preserve"] = _non_empty_string_list(policy.get("preserve"), "preserve", case_id)
    normalized["positive"] = _non_empty_string_list(policy.get("positive"), "positive", case_id)
    normalized["negative"] = _non_empty_string_list(policy.get("negative"), "negative", case_id)

    samples = policy.get("samples")
    if output in _OUTPUTS_REQUIRING_SAMPLES:
        normalized["samples"] = _non_empty_string_list(samples, "samples", case_id)
    elif samples is not None:
        normalized["samples"] = _non_empty_string_list(samples, "samples", case_id)

    if output in _OUTPUTS_REQUIRING_TOLERANCE:
        tolerance = policy.get("tolerance")
        if not isinstance(tolerance, str) or not tolerance.strip():
            raise AssertionPolicyError(
                f"{case_id} assertion policy {output} output requires a tolerance"
            )
        normalized["tolerance"] = tolerance.strip()
    elif "tolerance" in policy and policy["tolerance"] is not None:
        tolerance = policy["tolerance"]
        if not isinstance(tolerance, str) or not tolerance.strip():
            raise AssertionPolicyError(f"{case_id} assertion policy tolerance must be a string")
        normalized["tolerance"] = tolerance.strip()
    return normalized


def load_assertion_policy(
    suite: Mapping[str, Any], case_id: str | None = None,
) -> dict[str, Any]:
    """Load and validate the existing suite assertion table.

    With no ``case_id`` this returns ``{case_id: policy}`` for every suite
    case.  With a case ID it returns that single policy.  The suite case IDs
    and table keys must agree when both are present, preventing a case from
    being launched without the bounded target/preservation contract.
    """
    if not isinstance(suite, Mapping):
        raise AssertionPolicyError("timeline suite must be an object")
    table = suite.get("assertion_policy")
    if not isinstance(table, Mapping) or not table:
        raise AssertionPolicyError("timeline suite assertion_policy must be a non-empty object")

    cases = suite.get("cases")
    case_ids: set[str] | None = None
    if cases is not None:
        if not isinstance(cases, list) or any(not isinstance(case, Mapping) for case in cases):
            raise AssertionPolicyError("timeline suite cases must be a list of objects")
        case_ids = {str(case.get("id", "")) for case in cases}
        if "" in case_ids:
            raise AssertionPolicyError("timeline suite contains a case without an id")
        policy_ids = {str(key) for key in table}
        if policy_ids != case_ids:
            missing = sorted(case_ids - policy_ids)
            extra = sorted(policy_ids - case_ids)
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if extra:
                detail.append("extra=" + ",".join(extra))
            raise AssertionPolicyError(
                "assertion policy keys do not match suite cases (" + "; ".join(detail) + ")"
            )

    if case_id is not None:
        if case_id not in table:
            raise AssertionPolicyError(f"no assertion policy for case {case_id!r}")
        if case_ids is not None and case_id not in case_ids:
            raise AssertionPolicyError(f"case {case_id!r} is not present in suite cases")
        return validate_assertion_policy(table[case_id], case_id=case_id)

    return {
        str(key): validate_assertion_policy(value, case_id=str(key))
        for key, value in table.items()
    }


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: str
    message: str
    evidence: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "pass"


class DecodedMediaVerifier(Protocol):
    """Optional adapter for independent decoded-frame/audio verification."""

    def verify(self, check: Json, artifacts: Mapping[str, Any]) -> CheckResult: ...


def _path(value: Any, path: str) -> Any:
    """Resolve a conservative dotted path with optional numeric list indexes."""
    current = value
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise KeyError(path)
            current = current[index]
        else:
            raise KeyError(path)
    return current


def _artifact(artifacts: Mapping[str, Any], name: str) -> Any:
    if name not in artifacts:
        raise KeyError(f"missing artifact: {name}")
    return artifacts[name]


def check_path_equals(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Assert one artifact path equals its hidden expected value."""
    cid = str(check.get("id", "path_equals"))
    artifact_name = str(check.get("artifact", "after"))
    path = str(check.get("path", ""))
    try:
        actual = _path(_artifact(artifacts, artifact_name), path)
    except KeyError as exc:
        # L06's public result contract historically described these diagnostic
        # values as top-level observations, while an older hidden oracle
        # required an extra ``diagnostic`` wrapper.  Accept the documented
        # flat projection during regrade; do not make agents rediscover a
        # grader-only nesting convention.
        aliases = {
            "observations.diagnostic.status": "observations.candidate_status",
            "observations.diagnostic.error_type": "observations.candidate_error_type",
            "observations.diagnostic.base_parent_revision_id": "observations.base_parent_revision_id",
        }
        alias = aliases.get(path)
        if alias is None:
            return CheckResult(cid, "fail", str(exc), (artifact_name, path))
        try:
            actual = _path(_artifact(artifacts, artifact_name), alias)
        except KeyError:
            return CheckResult(cid, "fail", str(exc), (artifact_name, path))
    passed = actual == check.get("expected")
    return CheckResult(cid, "pass" if passed else "fail",
                       "value matches expected" if passed else "value differs from expected",
                       (f"{artifact_name}:{path}",))


def _mapping_contains(actual: Any, expected: Any) -> bool:
    """Return whether ``actual`` contains the explicitly required projection.

    Evaluation projections often add useful derived fields (for example local
    media paths or nested clips).  Identity checks must therefore compare the
    required fields, rather than requiring whole-object equality.
    """
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _mapping_contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    return actual == expected


def check_records_include(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Check required record projections while accepting extra fields.

    ``mode=all`` requires every expected record to occur in the actual list;
    ``mode=any`` is useful where the public brief asks the worker to expand
    one occurrence from a set of admitted targets.
    """
    cid = str(check.get("id", "records_include"))
    name = str(check.get("artifact", "after"))
    path = str(check.get("path", ""))
    try:
        actual = _path(_artifact(artifacts, name), path)
    except KeyError as exc:
        return CheckResult(cid, "fail", str(exc), (name, path))
    expected = check.get("expected", ())
    if not isinstance(actual, list) or not isinstance(expected, list):
        return CheckResult(cid, "fail", "record projection must be a list", (f"{name}:{path}",))
    def normalize(row: Any) -> Any:
        if not isinstance(row, Mapping):
            return row
        value = dict(row)
        # The public L02 projection already carries this identity under the
        # typed media handle; expose that equivalent field for the oracle.
        if "selected_image_media_id" not in value:
            handles = value.get("media_handles")
            if isinstance(handles, list):
                selected = next(
                    (item.get("media_id") for item in handles
                     if isinstance(item, Mapping) and item.get("role") == "selected_image"),
                    None,
                )
                if selected is not None:
                    value["selected_image_media_id"] = selected
        return value

    normalized = [normalize(row) for row in actual]
    matches = [any(_mapping_contains(row, required) for row in normalized) for required in expected]
    mode = str(check.get("mode", "all")).lower()
    if mode == "any":
        passed = bool(expected) and any(matches)
    else:
        passed = bool(expected) and all(matches)
    missing = sum(1 for value in matches if not value)
    return CheckResult(
        cid,
        "pass" if passed else "fail",
        "required record projection is present" if passed
        else f"required record projection is missing ({missing} expected record(s))",
        (f"{name}:{path}",),
    )


def check_semantic_oracle_unavailable(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Represent an unavailable semantic oracle explicitly, never as a pass."""
    cid = str(check.get("id", "semantic_oracle_unavailable"))
    return CheckResult(cid, "missing_capability", "semantic oracle is unavailable; result is not semantically graded")


def check_paths_unchanged(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Assert listed paths have identical values in before and after snapshots."""
    cid = str(check.get("id", "paths_unchanged"))
    try:
        before, after = _artifact(artifacts, "before"), _artifact(artifacts, "after")
        changed = [str(path) for path in check.get("paths", ())
                   if _path(before, str(path)) != _path(after, str(path))]
    except KeyError as exc:
        return CheckResult(cid, "fail", str(exc), ("before.json", "after.json"))
    return CheckResult(cid, "fail" if changed else "pass",
                       "protected values changed: " + ", ".join(changed) if changed
                       else "protected values are unchanged",
                       tuple(changed) if changed else tuple(str(x) for x in check.get("paths", ())))


def check_order(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Check exact order, including deterministic ID tie-breaking when requested."""
    cid = str(check.get("id", "order"))
    name = str(check.get("artifact", "after"))
    try:
        values = _path(_artifact(artifacts, name), str(check.get("path", "")))
    except KeyError as exc:
        return CheckResult(cid, "fail", str(exc), (name, str(check.get("path", ""))))
    if not isinstance(values, list):
        return CheckResult(cid, "fail", "ordered value is not a list", (name,))
    key = str(check.get("id_path", "id"))
    actual_ids = [_path(item, key) for item in values]
    expected_ids = list(check.get("expected_ids", ()))
    # If expected_ids is supplied it is the fixture's independently prepared
    # oracle.  Otherwise validate ascending key order for equal sort values.
    if expected_ids:
        passed = actual_ids == expected_ids
    else:
        sort_path = str(check.get("sort_path", "brightness"))
        pairs = [(_path(item, sort_path), _path(item, key)) for item in values]
        passed = pairs == sorted(pairs)
    return CheckResult(cid, "pass" if passed else "fail",
                       "ordering matches fixture policy" if passed else "ordering violates fixture policy",
                       (f"{name}:{check.get('path', '')}",))


def check_identity_disjoint(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Ensure duplicated entities have independent identities and references."""
    cid = str(check.get("id", "identity_disjoint"))
    try:
        before = _artifact(artifacts, str(check.get("before_artifact", "before")))
        after = _artifact(artifacts, str(check.get("after_artifact", "after")))
        original = set(_path(before, str(check.get("original_ids_path", "identity_ids"))))
        duplicate = set(_path(after, str(check.get("duplicate_ids_path", "duplicate.identity_ids"))))
    except KeyError as exc:
        return CheckResult(cid, "fail", str(exc), ("before.json", "after.json"))
    overlap = sorted(original & duplicate)
    passed = bool(duplicate) and not overlap
    return CheckResult(cid, "pass" if passed else "fail",
                       "duplicate identities are independent" if passed
                       else f"duplicate shares identities: {overlap or 'no duplicate identities found'}",
                       tuple(overlap))


def check_panel_coverage(check: Json, artifacts: Mapping[str, Any]) -> CheckResult:
    """Check required panel labels exist and have positive, in-bounds rectangles."""
    cid = str(check.get("id", "panel_coverage"))
    name = str(check.get("artifact", "after"))
    try:
        panels = _path(_artifact(artifacts, name), str(check.get("path", "panels")))
    except KeyError as exc:
        return CheckResult(cid, "fail", str(exc), (name, str(check.get("path", "panels"))))
    if not isinstance(panels, list):
        return CheckResult(cid, "fail", "panels value is not a list", (name,))
    label_path = str(check.get("label_path", "quadrant"))
    required = set(check.get("required", ("top_left", "top_right", "bottom_left", "bottom_right")))
    found: set[str] = set()
    invalid: list[str] = []
    rects: list[tuple[float, float, float, float, str]] = []
    width, height = float(check.get("width", 1)), float(check.get("height", 1))
    for panel in panels:
        try:
            label = str(_path(panel, label_path))
            rect = _path(panel, str(check.get("rect_path", "rect")))
            x, y, w, h = (float(rect[k]) for k in ("x", "y", "width", "height"))
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
                invalid.append(label)
            rects.append((x, y, w, h, label))
            found.add(label)
        except (KeyError, TypeError, ValueError):
            invalid.append("malformed")
    missing = sorted(required - found)
    # In-bounds rectangles whose areas sum to the canvas area and do not
    # overlap cover the complete canvas (including corners and edges).
    overlap = []
    for index, (x, y, w, h, label) in enumerate(rects):
        for ox, oy, ow, oh, other in rects[index + 1:]:
            if min(x + w, ox + ow) > max(x, ox) and min(y + h, oy + oh) > max(y, oy):
                overlap.append(f"{label}/{other}")
    area = sum(w * h for _, _, w, h, _ in rects)
    full_area = abs(area - width * height) <= float(check.get("area_tolerance", 1e-9))
    passed = not missing and not invalid
    passed = passed and not overlap and full_area
    return CheckResult(cid, "pass" if passed else "fail",
                       "all required panels are present and in bounds" if passed
                       else f"missing panels={missing}; invalid panels={invalid}; overlap={overlap}; full_area={full_area}",
                       tuple(missing + invalid + overlap))


def check_decoded_media(check: Json, artifacts: Mapping[str, Any],
                        verifier: DecodedMediaVerifier | None = None) -> CheckResult:
    """Delegate independent decoded pixels/audio checks or report capability gap."""
    cid = str(check.get("id", "decoded_media"))
    if verifier is None:
        return CheckResult(cid, "missing_capability",
                           "no decoded-media verifier was supplied; render bytes were not inspected")
    try:
        return verifier.verify(check, artifacts)
    except (FileNotFoundError, NotImplementedError) as exc:
        return CheckResult(cid, "missing_capability", str(exc) or "decoded-media capability unavailable")
    except Exception as exc:  # noqa: BLE001 - third-party adapters have no shared exception type.
        return CheckResult(cid, "fail", f"decoded-media verifier failed: {type(exc).__name__}: {exc}")


Checker = Callable[[Json, Mapping[str, Any]], CheckResult]
CHECKS: dict[str, Checker] = {
    "path_equals": check_path_equals,
    "records_include": check_records_include,
    "paths_unchanged": check_paths_unchanged,
    "order": check_order,
    "identity_disjoint": check_identity_disjoint,
    "panel_coverage": check_panel_coverage,
    "semantic_oracle_unavailable": check_semantic_oracle_unavailable,
}


def run_checks(checks: Iterable[Json], artifacts: Mapping[str, Any],
               verifier: DecodedMediaVerifier | None = None) -> list[CheckResult]:
    results = []
    for check in checks:
        check_type = str(check.get("check", ""))
        if check_type == "decoded_media":
            results.append(check_decoded_media(check, artifacts, verifier))
        elif check_type in CHECKS:
            results.append(CHECKS[check_type](check, artifacts))
        else:
            results.append(CheckResult(str(check.get("id", "unknown_check")),
                                       "missing_capability",
                                       f"unknown checker {check_type!r}"))
    return results
