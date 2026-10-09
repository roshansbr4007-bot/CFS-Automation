"""Phase 7.1 KRA configuration: the only write path for KRA plans, scoring rules, band schemes,
plan defaults and employee overrides. Every change is audited (performance.config.*) in the
same transaction. NOTHING here calculates a month (the KRA engine arrives in Phase 7.2).

Rules:
- HR prepares (configure_kpis), Admin approves: activate / retire (approve_kpi_config); the
  API layer checks who, these services check what.
- KRA configuration is editable only while DRAFT; ACTIVE / RETIRED rows are immutable (also at
  model level). A change needs a new version ("clone"). Activation needs a FUTURE effective
  date and complete configuration; incomplete drafts may be saved.
- Legacy (LEGACY_WEIGHTED) versions are read-only here, except that Admin may retire one
  (approved P13). The legacy services in apps.performance.services are unchanged.
- Plan for an employee on a date: an HR override (EmployeeKPIAssignment) covering the date;
  otherwise the department + system role (auth Group) default; otherwise no plan. Several
  login roles matching several defaults = ambiguous: HR must set an override.
- Changes that would alter a FINALIZED month are refused.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts import roles
from apps.audit.services import record
from apps.core.errors import ConflictError, FieldValidationError
from apps.core.timeutils import now_ist

from .models import (
    KPI,
    Band,
    BandScheme,
    CalculationModel,
    ComponentSource,
    ConfigurationLocked,
    DeductionKind,
    DeductionRule,
    DeductionScope,
    DeductionStacking,
    EmployeeKPIAssignment,
    KPIComponent,
    KPIPlanDefault,
    KPIWeight,
    KPIWeightVersion,
    ManualMatch,
    MonthlyPerformance,
    PerformanceStatus,
    ScoringRule,
    ScoringRuleStep,
    StackingMethod,
    TaskScope,
    VerificationPolicy,
    WeightVersionStatus,
)

TOTAL_POINTS = Decimal("10.00")
ONE_DAY = timedelta(days=1)
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
DRAFT, ACTIVE, RETIRED = (
    WeightVersionStatus.DRAFT,
    WeightVersionStatus.ACTIVE,
    WeightVersionStatus.RETIRED,
)
KRA = CalculationModel.KRA_POINTS
MANUAL_SCOPES = (TaskScope.MANUAL, TaskScope.BOTH)


class ConfigConflict(ConflictError):
    code = "kpi_config_conflict"
    message = "This configuration change is not possible in the current state."


# --- small helpers ----------------------------------------------------------------------------


def _conflict_on_race(func):
    """Two requests racing past the overlap / numbering checks hit a database constraint;
    report it as a conflict (409), never as a server error."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except IntegrityError as exc:
            raise ConfigConflict(
                "Someone else changed this configuration at the same time. Reload and retry."
            ) from exc

    return wrapper


def today() -> date:
    return now_ist().date()


def _audit(action, entity_type, entity_id, actor, *, old=None, new=None, extra=None):
    record(
        action=f"performance.config.{action}",
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        old=old,
        new=new,
        extra=extra,
    )


def _s(value):
    """JSON-safe audit value (dates as ISO text, decimals as exact text)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _code(value, field_name="code") -> str:
    code = (value or "").strip().upper()
    if not CODE_RE.match(code):
        raise FieldValidationError(
            fields={field_name: ["Use capital letters, digits and _ (start with a letter)."]}
        )
    return code


def _text(value, field_name, *, required=True, max_length=None) -> str:
    text = (value or "").strip()
    if required and not text:
        raise FieldValidationError(fields={field_name: ["This field is required."]})
    if max_length is not None and len(text) > max_length:
        raise FieldValidationError(
            fields={field_name: [f"Use at most {max_length} characters."]}
        )
    return text


def _decimal(value, field_name, *, low=None, high=None, places=2, positive=False):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FieldValidationError(fields={field_name: ["Enter a number."]}) from None
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise FieldValidationError(
            fields={field_name: [f"Use at most {places} decimal places."]}
        )
    if positive and number <= 0:
        raise FieldValidationError(fields={field_name: ["Must be greater than 0."]})
    if low is not None and number < low:
        raise FieldValidationError(fields={field_name: [f"Must be at least {low}."]})
    if high is not None and number > high:
        raise FieldValidationError(fields={field_name: [f"Must be at most {high}."]})
    return number


def _choice(value, choices, field_name, *, allow_blank=True) -> str:
    value = value or ""
    if value == "" and allow_blank:
        return ""
    if value not in choices.values:
        raise FieldValidationError(fields={field_name: [f"Unknown value: {value}."]})
    return value


def _period(effective_from, effective_to) -> None:
    if effective_to is not None and effective_to < effective_from:
        raise FieldValidationError(fields={"effective_to": ["Must be on or after the start."]})


def _overlaps(qs, start: date, end: date | None):
    """Rows of qs (with effective_from / effective_to) overlapping [start, end]."""
    qs = qs.exclude(effective_to__lt=start)
    if end is not None:
        qs = qs.exclude(effective_from__gt=end)
    return qs


def _covers(row, day: date) -> bool:
    return row.effective_from <= day and (row.effective_to is None or row.effective_to >= day)


def _months_overlapping(qs, start: date, end: date | None):
    qs = qs.filter(period_end__gte=start)
    if end is not None:
        qs = qs.filter(period_start__lte=end)
    return qs


# --- KPI master ---------------------------------------------------------------------------


def update_kpi_description(*, actor, kpi: KPI, description: str) -> KPI:
    """Only the description is editable: KPI codes and names are shared with legacy history
    (KRA names live on plan lines), and creating KPIs would change the legacy activation rule
    ("every active KPI"), so neither is offered here."""
    description = (description or "").strip()
    with transaction.atomic():
        kpi = KPI.objects.select_for_update().get(pk=kpi.pk)
        if description == kpi.description:
            return kpi
        old = kpi.description
        kpi.description = description
        kpi.save(update_fields=["description", "updated_at"])
        _audit("kpi_updated", "kpi", kpi.pk, actor, old={"description": old},
               new={"description": description})
    return kpi


# --- plan versions (KPIWeightVersion, calculation_model KRA_POINTS) -------------------------


def _lock_version(version) -> KPIWeightVersion:
    return KPIWeightVersion.objects.select_for_update().get(pk=version.pk)


def _require_kra(version) -> None:
    if version.calculation_model != KRA:
        raise ConfigConflict(
            "Legacy weight versions are read-only here.", code="legacy_configuration"
        )


def _require_kra_draft(version) -> None:
    _require_kra(version)
    if version.status != DRAFT:
        raise ConfigurationLocked()


def _plan_snapshot(version) -> dict:
    return {
        "configuration": version.configuration,
        "version": version.version,
        "name": version.name,
        "calculation_model": version.calculation_model,
        "status": version.status,
        "effective_from": _s(version.effective_from),
        "effective_to": _s(version.effective_to),
        "band_scheme_id": version.band_scheme_id,
        "credit_on_time": _s(version.credit_on_time),
        "credit_late": _s(version.credit_late),
        "credit_overdue": _s(version.credit_overdue),
        "deduction_stacking_method": version.deduction_stacking_method,
    }


def _clean_plan_values(values: dict) -> dict:
    clean = {}
    for name, value in values.items():
        if name == "name":
            clean[name] = _text(value, "name", max_length=120)
        elif name in ("credit_on_time", "credit_late", "credit_overdue"):
            clean[name] = _decimal(value, name, low=Decimal("0"), high=Decimal("1"))
        elif name == "deduction_stacking_method":
            clean[name] = _choice(value, StackingMethod, name)
        elif name == "band_scheme":
            if value is not None and value.status == RETIRED:
                raise FieldValidationError(fields={name: ["This band scheme is retired."]})
            clean[name] = value
        elif name in ("effective_from", "effective_to"):
            clean[name] = value
        else:  # programming error, never user input
            raise TypeError(f"Unknown plan field: {name}")
    return clean


PLAN_FIELDS = (
    "name", "effective_from", "effective_to", "band_scheme", "credit_on_time", "credit_late",
    "credit_overdue", "deduction_stacking_method",
)


@_conflict_on_race
def create_plan_version(
    *, actor, configuration: str, name: str, effective_from: date, effective_to=None,
    band_scheme=None, credit_on_time=Decimal("1.00"), credit_late=Decimal("0.25"),
    credit_overdue=Decimal("0.00"), deduction_stacking_method: str = "",
) -> KPIWeightVersion:
    """A new DRAFT KRA plan version; the number continues within its family. A family that
    holds legacy versions (e.g. OPERATIONS) never receives KRA versions."""
    configuration = _code(configuration, "configuration")
    values = _clean_plan_values({
        "name": name, "effective_from": effective_from, "effective_to": effective_to,
        "band_scheme": band_scheme, "credit_on_time": credit_on_time,
        "credit_late": credit_late, "credit_overdue": credit_overdue,
        "deduction_stacking_method": deduction_stacking_method,
    })
    _period(values["effective_from"], values["effective_to"])
    with transaction.atomic():
        family = list(
            KPIWeightVersion.objects.select_for_update().filter(configuration=configuration)
        )
        if any(v.calculation_model != KRA for v in family):
            raise ConfigConflict(
                f"{configuration} is a legacy configuration family; use another family code.",
                code="legacy_configuration",
            )
        version = KPIWeightVersion.objects.create(
            configuration=configuration,
            version=max((v.version for v in family), default=0) + 1,
            calculation_model=KRA,
            status=DRAFT,
            created_by=actor,
            **values,
        )
        _audit("plan_created", "kpi_plan_version", version.pk, actor, new=_plan_snapshot(version))
    return version


def update_plan_version(*, actor, version, **changes) -> KPIWeightVersion:
    values = _clean_plan_values(changes)
    with transaction.atomic():
        version = _lock_version(version)
        _require_kra_draft(version)
        old = _plan_snapshot(version)
        for name, value in values.items():
            setattr(version, name, value)
        _period(version.effective_from, version.effective_to)
        version.save()
        new = _plan_snapshot(version)
        changed = {k: v for k, v in new.items() if old[k] != v}
        if changed:
            _audit("plan_updated", "kpi_plan_version", version.pk, actor,
                   old={k: old[k] for k in changed}, new=changed)
    return version


def delete_plan_version(*, actor, version) -> None:
    """Only a DRAFT KRA version (never activated, so nothing refers to it) can be deleted."""
    with transaction.atomic():
        version = _lock_version(version)
        _require_kra_draft(version)
        if version.assignments.exists():
            raise ConfigConflict("Employee overrides refer to this version.")
        snapshot = _plan_snapshot(version)
        for line in version.weights.all():
            for component in line.components.all():
                component.delete()
            line.delete()
        for rule in version.deduction_rules.all():
            rule.delete()
        pk = version.pk
        version.delete()
        _audit("plan_deleted", "kpi_plan_version", pk, actor, old=snapshot)


@_conflict_on_race
def clone_plan_version(*, actor, version) -> KPIWeightVersion:
    """A new DRAFT version of the same family with every line, component and deduction rule
    copied (the way to change an ACTIVE plan)."""
    with transaction.atomic():
        source = _lock_version(version)
        _require_kra(source)
        latest = (
            KPIWeightVersion.objects.select_for_update()
            .filter(configuration=source.configuration)
            .order_by("-version")
            .first()
        )
        copy = KPIWeightVersion.objects.create(
            configuration=source.configuration,
            version=latest.version + 1,
            name=source.name,
            effective_from=source.effective_from,
            effective_to=source.effective_to,
            calculation_model=KRA,
            status=DRAFT,
            created_by=actor,
            band_scheme=source.band_scheme,
            credit_on_time=source.credit_on_time,
            credit_late=source.credit_late,
            credit_overdue=source.credit_overdue,
            deduction_stacking_method=source.deduction_stacking_method,
        )
        for line in source.weights.order_by("position", "id"):
            new_line = KPIWeight.objects.create(
                weight_version=copy, kpi=line.kpi, weight=line.weight,
                display_name=line.display_name, scoring_rule=line.scoring_rule,
                position=line.position,
            )
            for component in line.components.order_by("position", "id"):
                KPIComponent.objects.create(
                    plan_line=new_line,
                    position=component.position,
                    source_type=component.source_type,
                    responsibility=component.responsibility,
                    label=component.label,
                    contribution_share=component.contribution_share,
                    task_scope=component.task_scope,
                    manual_match=component.manual_match,
                    verification_policy=component.verification_policy,
                )
        for rule in source.deduction_rules.order_by("id"):
            DeductionRule.objects.create(
                plan_version=copy, code=rule.code, name=rule.name, kind=rule.kind,
                scope=rule.scope, min_pct=rule.min_pct, max_pct=rule.max_pct,
                ceiling_band=rule.ceiling_band, stacking=rule.stacking, cap_pct=rule.cap_pct,
                uncapped=rule.uncapped, priority=rule.priority, description=rule.description,
            )
        _audit("plan_cloned", "kpi_plan_version", copy.pk, actor,
               new=_plan_snapshot(copy), extra={"source_version_id": source.pk})
    return copy


def plan_activation_problems(version) -> list[str]:
    """Everything that stops a KRA draft from being activated (empty = ready)."""
    problems = []
    start = version.effective_from
    if start <= today():
        problems.append("The effective date must be in the future.")
    if version.effective_to is not None and version.effective_to < start:
        problems.append("The end date is before the start date.")
    for name in ("credit_on_time", "credit_late", "credit_overdue"):
        if getattr(version, name) is None:
            problems.append(f"Set the task credit {name}.")
    scheme = version.band_scheme
    if scheme is None:
        problems.append("Choose a band scheme.")
    elif scheme.status != ACTIVE or not _covers(scheme, start):
        problems.append(f"Band scheme {scheme} must be ACTIVE on {start}.")

    lines = list(
        version.weights.select_related("kpi", "scoring_rule").order_by("position", "id")
    )
    if not lines:
        problems.append("Add the KPI lines.")
    total = sum((line.weight for line in lines), Decimal("0"))
    if lines and total != TOTAL_POINTS:
        problems.append(f"Line weights must total exactly 10.00 (they total {total}).")
    for line in lines:
        name = line.label
        if not line.kpi.is_active:
            problems.append(f"{name}: the KPI is inactive.")
        rule = line.scoring_rule
        if rule is None:
            problems.append(f"{name}: choose a scoring rule.")
        elif rule.status != ACTIVE or not _covers(rule, start):
            problems.append(f"{name}: scoring rule {rule} must be ACTIVE on {start}.")
        components = list(line.components.select_related("responsibility__template"))
        if not components:
            problems.append(f"{name}: add at least one component.")
        type_keys, category_keys = {}, {}
        for component in components:
            problems.extend(_component_problems(name, component))
            if component.source_type != ComponentSource.RESPONSIBILITY_TASKS:
                continue
            if component.task_scope not in MANUAL_SCOPES:
                continue
            resp = component.responsibility
            if component.manual_match == ManualMatch.TASK_TYPE and resp.template_id:
                if resp.template_id in type_keys:
                    problems.append(
                        f"{name}: {resp.name} and {type_keys[resp.template_id]} match manual "
                        "tasks by the same task type."
                    )
                type_keys.setdefault(resp.template_id, resp.name)
            if component.manual_match == ManualMatch.CATEGORY:
                key = (resp.category_id, resp.department_id)
                if key in category_keys:
                    problems.append(
                        f"{name}: {resp.name} and {category_keys[key]} match manual tasks by "
                        "the same category and department."
                    )
                category_keys.setdefault(key, resp.name)

    rules = list(version.deduction_rules.select_related("ceiling_band").order_by("code"))
    priorities = {}
    for rule in rules:
        label = f"Deduction {rule.code}"
        if not rule.scope:
            problems.append(f"{label}: choose the scope.")
        if not rule.stacking:
            problems.append(f"{label}: choose stacking or non-stacking.")
        if rule.priority is None:
            problems.append(f"{label}: set the priority.")
        elif rule.priority in priorities:
            problems.append(
                f"{label}: priority {rule.priority} is also used by {priorities[rule.priority]}."
            )
        else:
            priorities[rule.priority] = rule.code
        if rule.cap_pct is None and not rule.uncapped:
            problems.append(f"{label}: set a cap or mark it uncapped.")
        if rule.kind == DeductionKind.BAND_CEILING:
            if rule.ceiling_band_id is None:
                problems.append(f"{label}: choose the ceiling band.")
            elif rule.ceiling_band.scheme_id != version.band_scheme_id:
                problems.append(f"{label}: the ceiling band is not in the plan's band scheme.")
    if rules and not version.deduction_stacking_method:
        problems.append("Choose how stacking deductions combine (deduction stacking method).")

    clash = _overlaps(
        KPIWeightVersion.objects.filter(configuration=version.configuration, status=ACTIVE)
        .exclude(pk=version.pk),
        start,
        version.effective_to,
    )
    for other in clash:
        problems.append(f"{other} is ACTIVE for part of this period; retire it first.")
    return problems


def _component_problems(line_name, component) -> list[str]:
    problems = []
    if component.source_type == ComponentSource.MANUAL_ENTRY:
        return problems  # label enforced by the database
    resp = component.responsibility
    where = f"{line_name} / {resp.name}"
    if not resp.is_active:
        problems.append(f"{where}: the responsibility is inactive.")
    if not component.task_scope:
        problems.append(f"{where}: choose the task scope.")
    if not component.verification_policy:
        problems.append(f"{where}: choose the verification policy.")
    if component.task_scope in MANUAL_SCOPES:
        if not component.manual_match:
            problems.append(f"{where}: choose how manual tasks are matched.")
        elif component.manual_match == ManualMatch.TASK_TYPE and resp.template_id is None:
            problems.append(f"{where}: the responsibility has no task type to match.")
    if component.verification_policy == VerificationPolicy.REQUIRED:
        # Phase 7.2 decision D5: verification-required scoring needs tasks that can be verified.
        template = resp.template
        if template is None or not template.verification_required:
            problems.append(
                f"{where}: verification is required, but the responsibility's task type has no "
                "verification step."
            )
        if (component.task_scope in MANUAL_SCOPES
                and component.manual_match == ManualMatch.CATEGORY):
            problems.append(
                f"{where}: verification is required, so manual tasks cannot be matched by "
                "category (such tasks may have no verification step)."
            )
    return problems


def activate_plan_version(*, actor, version) -> KPIWeightVersion:
    """Admin: DRAFT -> ACTIVE once the configuration is complete. Never automatic."""
    with transaction.atomic():
        version = _lock_version(version)
        _require_kra(version)
        if version.status != DRAFT:
            raise ConfigConflict("Only a draft plan can be activated.")
        # Lock what the checks rely on, so a concurrent retirement cannot slip in between.
        list(ScoringRule.objects.select_for_update().filter(
            pk__in=version.weights.values("scoring_rule_id")
        ))
        if version.band_scheme_id:
            list(BandScheme.objects.select_for_update().filter(pk=version.band_scheme_id))
        problems = plan_activation_problems(version)
        if problems:
            raise FieldValidationError(
                "This plan cannot be activated yet.", fields={"activation": problems}
            )
        version.status = ACTIVE
        version.activated_by = actor
        version.activated_at = timezone.now()
        version.save(update_fields=["status", "activated_by", "activated_at"])
        _audit("plan_activated", "kpi_plan_version", version.pk, actor,
               old={"status": DRAFT}, new=_plan_snapshot(version))
    return version


def retire_plan_version(*, actor, version, last_day: date, reason: str) -> KPIWeightVersion:
    """Admin: ACTIVE -> RETIRED with a last day (legacy or KRA; approved P13 retires the legacy
    OPERATIONS v1 this way, audited, instead of by migration). Its lines never change."""
    reason = _text(reason, "reason")
    with transaction.atomic():
        version = _lock_version(version)
        if version.status != ACTIVE:
            raise ConfigConflict("Only an active version can be retired.")
        if last_day < version.effective_from:
            raise FieldValidationError(fields={"last_day": ["Cannot end before it starts."]})
        if version.effective_to is not None and last_day > version.effective_to:
            raise FieldValidationError(
                fields={"last_day": ["Must be on or before the version's current last day."]}
            )
        if _overlaps(version.assignments.all(), last_day + ONE_DAY, None).exists():
            raise ConfigConflict(
                "Employee overrides of this version run past that day; end them first."
            )
        if MonthlyPerformance.objects.filter(
            weight_version=version, period_end__gt=last_day,
            status=PerformanceStatus.FINALIZED,
        ).exists():
            # Phase 7.2: non-final months are recalculated with the plan of their last day (D9)
            raise ConfigConflict("Finalized monthly records of this version run past that day.")
        old = {"status": version.status, "effective_to": _s(version.effective_to)}
        version.status = RETIRED
        version.effective_to = last_day
        version.retired_by = actor
        version.retired_at = timezone.now()
        version.retire_reason = reason
        version.save(
            update_fields=["status", "effective_to", "retired_by", "retired_at", "retire_reason"]
        )
        _audit("plan_retired", "kpi_plan_version", version.pk, actor, old=old,
               new={"status": RETIRED, "effective_to": last_day.isoformat(), "reason": reason},
               extra={"calculation_model": version.calculation_model})
    return version


# --- plan lines --------------------------------------------------------------------------------


def _line_snapshot(line) -> dict:
    return {
        "plan_version_id": line.weight_version_id,
        "kpi": line.kpi.code,
        "weight": _s(line.weight),
        "display_name": line.display_name,
        "scoring_rule_id": line.scoring_rule_id,
        "position": line.position,
    }


def _clean_line_values(values: dict) -> dict:
    clean = {}
    for name, value in values.items():
        if name == "weight":
            clean[name] = _decimal(value, "weight", low=Decimal("0"), high=TOTAL_POINTS)
        elif name == "display_name":
            clean[name] = _text(value, name, required=False, max_length=120)
        elif name == "scoring_rule":
            if value is not None and value.status == RETIRED:
                raise FieldValidationError(fields={name: ["This scoring rule is retired."]})
            clean[name] = value
        elif name == "position":
            clean[name] = int(value)
        else:
            raise TypeError(f"Unknown line field: {name}")
    return clean


@_conflict_on_race
def create_plan_line(
    *, actor, version, kpi: KPI, weight, display_name: str = "", scoring_rule=None,
    position: int | None = None,
) -> KPIWeight:
    values = _clean_line_values({"weight": weight, "display_name": display_name,
                                 "scoring_rule": scoring_rule})
    if not kpi.is_active:
        raise FieldValidationError(fields={"kpi": ["This KPI is inactive."]})
    with transaction.atomic():
        version = _lock_version(version)
        _require_kra_draft(version)
        if version.weights.filter(kpi=kpi).exists():
            raise ConfigConflict("This KPI already has a line in this plan.")
        line = KPIWeight.objects.create(
            weight_version=version, kpi=kpi,
            position=position if position is not None else version.weights.count() + 1,
            **values,
        )
        _audit("line_created", "kpi_plan_line", line.pk, actor, new=_line_snapshot(line))
    return line


def update_plan_line(*, actor, line, **changes) -> KPIWeight:
    values = _clean_line_values(changes)
    with transaction.atomic():
        version = _lock_version(line.weight_version)
        _require_kra_draft(version)
        line = KPIWeight.objects.select_related("kpi").get(pk=line.pk)
        old = _line_snapshot(line)
        for name, value in values.items():
            setattr(line, name, value)
        line.save()
        new = _line_snapshot(line)
        changed = {k: v for k, v in new.items() if old[k] != v}
        if changed:
            _audit("line_updated", "kpi_plan_line", line.pk, actor,
                   old={k: old[k] for k in changed}, new=changed)
    return line


def delete_plan_line(*, actor, line) -> None:
    with transaction.atomic():
        version = _lock_version(line.weight_version)
        _require_kra_draft(version)
        line = KPIWeight.objects.select_related("kpi").get(pk=line.pk)
        snapshot = _line_snapshot(line)
        for component in line.components.all():
            component.delete()
        pk = line.pk
        line.delete()
        _audit("line_deleted", "kpi_plan_line", pk, actor, old=snapshot)


# --- components ----------------------------------------------------------------------------


COMPONENT_FIELDS = (
    "source_type", "responsibility", "label", "contribution_share", "task_scope",
    "manual_match", "verification_policy", "position",
)


def _component_snapshot(component) -> dict:
    return {
        "plan_line_id": component.plan_line_id,
        "source_type": component.source_type,
        "responsibility_id": component.responsibility_id,
        "label": component.label,
        "contribution_share": _s(component.contribution_share),
        "task_scope": component.task_scope,
        "manual_match": component.manual_match,
        "verification_policy": component.verification_policy,
        "position": component.position,
    }


def _clean_component(values: dict) -> dict:
    """Validate a complete set of component values. A draft may leave task scope, manual
    matching and verification empty (activation requires them)."""
    source = _choice(values.get("source_type"), ComponentSource, "source_type", allow_blank=False)
    resp = values.get("responsibility")
    label = _text(values.get("label"), "label", required=False, max_length=120)
    share = _decimal(values.get("contribution_share"), "contribution_share", places=6,
                     positive=True, high=Decimal("999"))
    scope = _choice(values.get("task_scope"), TaskScope, "task_scope")
    match = _choice(values.get("manual_match"), ManualMatch, "manual_match")
    policy = _choice(values.get("verification_policy"), VerificationPolicy, "verification_policy")
    if source == ComponentSource.MANUAL_ENTRY:
        if resp is not None:
            raise FieldValidationError(
                fields={"responsibility": ["A manual-entry component has no responsibility."]}
            )
        if not label:
            raise FieldValidationError(fields={"label": ["Name the manual-entry component."]})
        if scope or match or policy:
            raise FieldValidationError(
                fields={"source_type": [
                    "A manual-entry component has no task scope, matching or verification."
                ]}
            )
    else:
        if resp is None:
            raise FieldValidationError(fields={"responsibility": ["Choose the responsibility."]})
        if not resp.is_active:
            raise FieldValidationError(
                fields={"responsibility": ["This responsibility is inactive."]}
            )
        if scope == TaskScope.SCHEDULED and match not in ("", ManualMatch.NONE):
            raise FieldValidationError(
                fields={"manual_match": ["Scheduled-only components do not match manual tasks."]}
            )
        if scope == TaskScope.SCHEDULED:
            match = ""
        if match == ManualMatch.TASK_TYPE and resp.template_id is None:
            raise FieldValidationError(
                fields={"manual_match": ["This responsibility has no task type to match."]}
            )
    return {
        "source_type": source, "responsibility": resp, "label": label,
        "contribution_share": share, "task_scope": scope, "manual_match": match,
        "verification_policy": policy,
    }


def _line_version(line) -> KPIWeightVersion:
    version = _lock_version(line.weight_version)
    _require_kra_draft(version)
    return version


@_conflict_on_race
def create_component(
    *, actor, line, source_type, responsibility=None, label="", contribution_share=Decimal("1"),
    task_scope="", manual_match="", verification_policy="", position=None,
) -> KPIComponent:
    values = _clean_component({
        "source_type": source_type, "responsibility": responsibility, "label": label,
        "contribution_share": contribution_share, "task_scope": task_scope,
        "manual_match": manual_match, "verification_policy": verification_policy,
    })
    with transaction.atomic():
        _line_version(line)
        if values["responsibility"] is not None and line.components.filter(
            responsibility=values["responsibility"]
        ).exists():
            raise ConfigConflict("This responsibility is already a component of this line.")
        component = KPIComponent.objects.create(
            plan_line=line,
            position=position if position is not None else line.components.count() + 1,
            **values,
        )
        _audit("component_created", "kpi_component", component.pk, actor,
               new=_component_snapshot(component))
    return component


def update_component(*, actor, component, **changes) -> KPIComponent:
    unknown = set(changes) - set(COMPONENT_FIELDS)
    if unknown:
        raise TypeError(f"Unknown component fields: {sorted(unknown)}")
    with transaction.atomic():
        component = KPIComponent.objects.select_related("plan_line").get(pk=component.pk)
        _line_version(component.plan_line)
        changes = dict(changes)
        position = changes.pop("position", None)
        current = {
            name: getattr(component, name) for name in COMPONENT_FIELDS if name != "position"
        }
        values = _clean_component({**current, **changes})
        resp = values["responsibility"]
        if resp is not None and component.plan_line.components.filter(
            responsibility=resp
        ).exclude(pk=component.pk).exists():
            raise ConfigConflict("This responsibility is already a component of this line.")
        old = _component_snapshot(component)
        for name, value in values.items():
            setattr(component, name, value)
        if position is not None:
            component.position = int(position)
        component.save()
        new = _component_snapshot(component)
        changed = {k: v for k, v in new.items() if old[k] != v}
        if changed:
            _audit("component_updated", "kpi_component", component.pk, actor,
                   old={k: old[k] for k in changed}, new=changed)
    return component


def delete_component(*, actor, component) -> None:
    with transaction.atomic():
        component = KPIComponent.objects.select_related("plan_line").get(pk=component.pk)
        _line_version(component.plan_line)
        snapshot = _component_snapshot(component)
        pk = component.pk
        component.delete()
        _audit("component_deleted", "kpi_component", pk, actor, old=snapshot)


# --- deduction rules -------------------------------------------------------------------------


DEDUCTION_FIELDS = (
    "name", "kind", "scope", "min_pct", "max_pct", "ceiling_band", "stacking", "cap_pct",
    "uncapped", "priority", "description",
)


def _deduction_snapshot(rule) -> dict:
    return {
        "plan_version_id": rule.plan_version_id,
        "code": rule.code,
        "name": rule.name,
        "kind": rule.kind,
        "scope": rule.scope,
        "min_pct": _s(rule.min_pct),
        "max_pct": _s(rule.max_pct),
        "ceiling_band_id": rule.ceiling_band_id,
        "stacking": rule.stacking,
        "cap_pct": _s(rule.cap_pct),
        "uncapped": rule.uncapped,
        "priority": rule.priority,
        "description": rule.description,
    }


def _clean_deduction(values: dict, version) -> dict:
    """No penalty arithmetic is configured or assumed here: the range bounds the percentage HR
    may apply; scope, stacking, cap, priority and ceiling band are HR's configuration."""
    kind = _choice(values.get("kind"), DeductionKind, "kind", allow_blank=False)
    hundred = Decimal("100")
    low = _decimal(values.get("min_pct"), "min_pct", low=Decimal("0"), high=hundred)
    high = _decimal(values.get("max_pct"), "max_pct", low=Decimal("0"), high=hundred)
    band = values.get("ceiling_band")
    cap = _decimal(values.get("cap_pct"), "cap_pct", high=hundred, positive=True)
    uncapped = bool(values.get("uncapped"))
    priority = values.get("priority")
    if kind == DeductionKind.PERCENT_RANGE:
        if low is None or high is None:
            raise FieldValidationError(fields={"min_pct": ["Give the percentage range."]})
        if low > high:
            raise FieldValidationError(fields={"max_pct": ["Must be at least the minimum."]})
        if band is not None:
            raise FieldValidationError(
                fields={"ceiling_band": ["Only a band-ceiling rule has a ceiling band."]}
            )
    else:
        if low is not None or high is not None:
            raise FieldValidationError(
                fields={"min_pct": ["A band-ceiling rule has no percentage range."]}
            )
        if band is not None and band.scheme_id != version.band_scheme_id:
            raise FieldValidationError(
                fields={"ceiling_band": ["Choose a band of the plan's band scheme."]}
            )
    if cap is not None and uncapped:
        raise FieldValidationError(fields={"cap_pct": ["A capped rule cannot be uncapped."]})
    if priority is not None and int(priority) < 1:
        raise FieldValidationError(fields={"priority": ["Use 1 or more."]})
    return {
        "name": _text(values.get("name"), "name", max_length=120),
        "kind": kind,
        "scope": _choice(values.get("scope"), DeductionScope, "scope"),
        "min_pct": low,
        "max_pct": high,
        "ceiling_band": band,
        "stacking": _choice(values.get("stacking"), DeductionStacking, "stacking"),
        "cap_pct": cap,
        "uncapped": uncapped,
        "priority": int(priority) if priority is not None else None,
        "description": (values.get("description") or "").strip(),
    }


@_conflict_on_race
def create_deduction_rule(*, actor, version, code: str, **values) -> DeductionRule:
    unknown = set(values) - set(DEDUCTION_FIELDS)
    if unknown:
        raise TypeError(f"Unknown deduction fields: {sorted(unknown)}")
    code = _code(code)
    with transaction.atomic():
        version = _lock_version(version)
        _require_kra_draft(version)
        clean = _clean_deduction(values, version)
        if version.deduction_rules.filter(code=code).exists():
            raise ConfigConflict("This plan already has a deduction rule with this code.")
        rule = DeductionRule.objects.create(plan_version=version, code=code, **clean)
        _audit("deduction_rule_created", "kpi_deduction_rule", rule.pk, actor,
               new=_deduction_snapshot(rule))
    return rule


def update_deduction_rule(*, actor, rule, **changes) -> DeductionRule:
    unknown = set(changes) - set(DEDUCTION_FIELDS)
    if unknown:
        raise TypeError(f"Unknown deduction fields: {sorted(unknown)}")
    with transaction.atomic():
        version = _lock_version(rule.plan_version)
        _require_kra_draft(version)
        rule = DeductionRule.objects.get(pk=rule.pk)
        current = {name: getattr(rule, name) for name in DEDUCTION_FIELDS}
        clean = _clean_deduction({**current, **changes}, version)
        old = _deduction_snapshot(rule)
        for name, value in clean.items():
            setattr(rule, name, value)
        rule.save()
        new = _deduction_snapshot(rule)
        changed = {k: v for k, v in new.items() if old[k] != v}
        if changed:
            _audit("deduction_rule_updated", "kpi_deduction_rule", rule.pk, actor,
                   old={k: old[k] for k in changed}, new=changed)
    return rule


def delete_deduction_rule(*, actor, rule) -> None:
    with transaction.atomic():
        version = _lock_version(rule.plan_version)
        _require_kra_draft(version)
        rule = DeductionRule.objects.get(pk=rule.pk)
        snapshot = _deduction_snapshot(rule)
        pk = rule.pk
        rule.delete()
        _audit("deduction_rule_deleted", "kpi_deduction_rule", pk, actor, old=snapshot)


# --- scoring rules and band schemes (shared versioned-config mechanics) ---------------------


def _versioned_snapshot(row) -> dict:
    return {
        "code": row.code,
        "version": row.version,
        "name": row.name,
        "status": row.status,
        "effective_from": _s(row.effective_from),
        "effective_to": _s(row.effective_to),
    }


def _rule_snapshot(rule) -> dict:
    return {
        **_versioned_snapshot(rule),
        "below_min_score_pct": _s(rule.below_min_score_pct),
        "steps": [
            [_s(s.min_achievement_pct), _s(s.score_pct)]
            for s in rule.steps.order_by("-min_achievement_pct")
        ],
    }


def _scheme_snapshot(scheme) -> dict:
    return {
        **_versioned_snapshot(scheme),
        "bands": [
            [b.name, _s(b.min_points), b.position] for b in scheme.bands.order_by("-min_points")
        ],
    }


def _clean_steps(steps) -> list[tuple[Decimal, Decimal]]:
    hundred = Decimal("100")
    clean, seen = [], set()
    for index, step in enumerate(steps or []):
        low = _decimal(step.get("min_achievement_pct"), f"steps.{index}.min_achievement_pct",
                       low=Decimal("0"), high=hundred)
        score = _decimal(step.get("score_pct"), f"steps.{index}.score_pct",
                         low=Decimal("0"), high=hundred)
        if low is None or score is None:
            raise FieldValidationError(fields={f"steps.{index}": ["Give both values."]})
        if low in seen:
            raise FieldValidationError(
                fields={f"steps.{index}.min_achievement_pct": ["Each minimum must be unique."]}
            )
        seen.add(low)
        clean.append((low, score))
    return clean


def _clean_bands(bands) -> list[tuple[str, Decimal, int]]:
    clean, names, minimums = [], set(), set()
    for index, band in enumerate(bands or []):
        name = _text(band.get("name"), f"bands.{index}.name", max_length=60)
        low = _decimal(band.get("min_points"), f"bands.{index}.min_points",
                       low=Decimal("0"), high=TOTAL_POINTS)
        if low is None:
            raise FieldValidationError(fields={f"bands.{index}.min_points": ["Required."]})
        if name.lower() in names or low in minimums:
            raise FieldValidationError(
                fields={f"bands.{index}": ["Band names and minimum points must be unique."]}
            )
        names.add(name.lower())
        minimums.add(low)
        position = band.get("position")
        clean.append((name, low, int(position) if position is not None else index + 1))
    return clean


def _next_version(model, code) -> int:
    latest = model.objects.select_for_update().filter(code=code).order_by("-version").first()
    return (latest.version + 1) if latest else 1


@_conflict_on_race
def create_scoring_rule(
    *, actor, code: str, name: str, effective_from: date, effective_to=None,
    below_min_score_pct=None, steps=(),
) -> ScoringRule:
    code = _code(code)
    name = _text(name, "name", max_length=120)
    below = _decimal(below_min_score_pct, "below_min_score_pct", low=Decimal("0"),
                     high=Decimal("100"))
    clean_steps = _clean_steps(steps)
    _period(effective_from, effective_to)
    with transaction.atomic():
        rule = ScoringRule.objects.create(
            code=code, version=_next_version(ScoringRule, code), name=name, status=DRAFT,
            effective_from=effective_from, effective_to=effective_to,
            below_min_score_pct=below, created_by=actor,
        )
        for low, score in clean_steps:
            ScoringRuleStep.objects.create(rule=rule, min_achievement_pct=low, score_pct=score)
        _audit("scoring_rule_created", "kpi_scoring_rule", rule.pk, actor,
               new=_rule_snapshot(rule))
    return rule


def update_scoring_rule(*, actor, rule, steps=None, **changes) -> ScoringRule:
    with transaction.atomic():
        rule = ScoringRule.objects.select_for_update().get(pk=rule.pk)
        if rule.status != DRAFT:
            raise ConfigurationLocked()
        old = _rule_snapshot(rule)
        for name, value in changes.items():
            if name == "name":
                rule.name = _text(value, "name", max_length=120)
            elif name == "below_min_score_pct":
                rule.below_min_score_pct = _decimal(
                    value, name, low=Decimal("0"), high=Decimal("100")
                )
            elif name in ("effective_from", "effective_to"):
                setattr(rule, name, value)
            else:
                raise TypeError(f"Unknown scoring rule field: {name}")
        _period(rule.effective_from, rule.effective_to)
        rule.save()
        if steps is not None:
            clean_steps = _clean_steps(steps)
            for step in rule.steps.all():
                step.delete()
            for low, score in clean_steps:
                ScoringRuleStep.objects.create(
                    rule=rule, min_achievement_pct=low, score_pct=score
                )
        new = _rule_snapshot(rule)
        if new != old:
            _audit("scoring_rule_updated", "kpi_scoring_rule", rule.pk, actor, old=old, new=new)
    return rule


@_conflict_on_race
def clone_scoring_rule(*, actor, rule) -> ScoringRule:
    with transaction.atomic():
        source = ScoringRule.objects.select_for_update().get(pk=rule.pk)
        copy = ScoringRule.objects.create(
            code=source.code, version=_next_version(ScoringRule, source.code),
            name=source.name, status=DRAFT, effective_from=source.effective_from,
            effective_to=source.effective_to, below_min_score_pct=source.below_min_score_pct,
            created_by=actor,
        )
        for step in source.steps.all():
            ScoringRuleStep.objects.create(
                rule=copy, min_achievement_pct=step.min_achievement_pct, score_pct=step.score_pct
            )
        _audit("scoring_rule_cloned", "kpi_scoring_rule", copy.pk, actor,
               new=_rule_snapshot(copy), extra={"source_rule_id": source.pk})
    return copy


def _activation_basics(row, model, kind: str) -> list[str]:
    problems = []
    if row.effective_from <= today():
        problems.append("The effective date must be in the future.")
    clash = _overlaps(
        model.objects.filter(code=row.code, status=ACTIVE).exclude(pk=row.pk),
        row.effective_from,
        row.effective_to,
    )
    for other in clash:
        problems.append(f"{kind} {other} is ACTIVE for part of this period; retire it first.")
    return problems


def activate_scoring_rule(*, actor, rule) -> ScoringRule:
    with transaction.atomic():
        rule = ScoringRule.objects.select_for_update().get(pk=rule.pk)
        if rule.status != DRAFT:
            raise ConfigConflict("Only a draft scoring rule can be activated.")
        problems = _activation_basics(rule, ScoringRule, "Scoring rule")
        if not rule.steps.exists():
            problems.append("Add the benchmark steps.")
        if rule.below_min_score_pct is None:
            problems.append("Set the score below the lowest step.")
        if problems:
            raise FieldValidationError(
                "This scoring rule cannot be activated yet.", fields={"activation": problems}
            )
        rule.status = ACTIVE
        rule.activated_by = actor
        rule.activated_at = timezone.now()
        rule.save(update_fields=["status", "activated_by", "activated_at"])
        _audit("scoring_rule_activated", "kpi_scoring_rule", rule.pk, actor,
               old={"status": DRAFT}, new=_rule_snapshot(rule))
    return rule


def _retire(row, *, actor, last_day: date, reason: str, in_use, action: str, entity: str):
    reason = _text(reason, "reason")
    if row.status != ACTIVE:
        raise ConfigConflict("Only an active version can be retired.")
    if last_day < row.effective_from:
        raise FieldValidationError(fields={"last_day": ["Cannot end before it starts."]})
    if row.effective_to is not None and last_day > row.effective_to:
        raise FieldValidationError(
            fields={"last_day": ["Must be on or before the current last day."]}
        )
    users = [str(v) for v in in_use if v.effective_to is None or v.effective_to > last_day]
    if users:
        raise ConfigConflict(
            f"Active plans use it after that day: {', '.join(users)}. Retire them first."
        )
    old = {"status": row.status, "effective_to": _s(row.effective_to)}
    row.status = RETIRED
    row.effective_to = last_day
    row.retired_by = actor
    row.retired_at = timezone.now()
    row.retire_reason = reason
    row.save(update_fields=["status", "effective_to", "retired_by", "retired_at", "retire_reason"])
    _audit(action, entity, row.pk, actor, old=old,
           new={"status": RETIRED, "effective_to": last_day.isoformat(), "reason": reason})
    return row


def retire_scoring_rule(*, actor, rule, last_day: date, reason: str) -> ScoringRule:
    with transaction.atomic():
        rule = ScoringRule.objects.select_for_update().get(pk=rule.pk)
        in_use = KPIWeightVersion.objects.filter(
            status=ACTIVE, weights__scoring_rule=rule
        ).distinct()
        return _retire(rule, actor=actor, last_day=last_day, reason=reason, in_use=in_use,
                       action="scoring_rule_retired", entity="kpi_scoring_rule")


@_conflict_on_race
def create_band_scheme(
    *, actor, code: str, name: str, effective_from: date, effective_to=None, bands=(),
) -> BandScheme:
    code = _code(code)
    name = _text(name, "name", max_length=120)
    clean_bands = _clean_bands(bands)
    _period(effective_from, effective_to)
    with transaction.atomic():
        scheme = BandScheme.objects.create(
            code=code, version=_next_version(BandScheme, code), name=name, status=DRAFT,
            effective_from=effective_from, effective_to=effective_to, created_by=actor,
        )
        for band_name, low, position in clean_bands:
            Band.objects.create(scheme=scheme, name=band_name, min_points=low, position=position)
        _audit("band_scheme_created", "kpi_band_scheme", scheme.pk, actor,
               new=_scheme_snapshot(scheme))
    return scheme


def update_band_scheme(*, actor, scheme, bands=None, **changes) -> BandScheme:
    with transaction.atomic():
        scheme = BandScheme.objects.select_for_update().get(pk=scheme.pk)
        if scheme.status != DRAFT:
            raise ConfigurationLocked()
        old = _scheme_snapshot(scheme)
        for name, value in changes.items():
            if name == "name":
                scheme.name = _text(value, "name", max_length=120)
            elif name in ("effective_from", "effective_to"):
                setattr(scheme, name, value)
            else:
                raise TypeError(f"Unknown band scheme field: {name}")
        _period(scheme.effective_from, scheme.effective_to)
        scheme.save()
        if bands is not None:
            clean_bands = _clean_bands(bands)
            in_use = DeductionRule.objects.filter(ceiling_band__scheme=scheme)
            if in_use.exists():
                raise ConfigConflict(
                    "Deduction rules use these bands as a ceiling; change those rules first."
                )
            for band in scheme.bands.all():
                band.delete()
            for band_name, low, position in clean_bands:
                Band.objects.create(
                    scheme=scheme, name=band_name, min_points=low, position=position
                )
        new = _scheme_snapshot(scheme)
        if new != old:
            _audit("band_scheme_updated", "kpi_band_scheme", scheme.pk, actor, old=old, new=new)
    return scheme


@_conflict_on_race
def clone_band_scheme(*, actor, scheme) -> BandScheme:
    with transaction.atomic():
        source = BandScheme.objects.select_for_update().get(pk=scheme.pk)
        copy = BandScheme.objects.create(
            code=source.code, version=_next_version(BandScheme, source.code),
            name=source.name, status=DRAFT, effective_from=source.effective_from,
            effective_to=source.effective_to, created_by=actor,
        )
        for band in source.bands.all():
            Band.objects.create(
                scheme=copy, name=band.name, min_points=band.min_points, position=band.position
            )
        _audit("band_scheme_cloned", "kpi_band_scheme", copy.pk, actor,
               new=_scheme_snapshot(copy), extra={"source_scheme_id": source.pk})
    return copy


def activate_band_scheme(*, actor, scheme) -> BandScheme:
    with transaction.atomic():
        scheme = BandScheme.objects.select_for_update().get(pk=scheme.pk)
        if scheme.status != DRAFT:
            raise ConfigConflict("Only a draft band scheme can be activated.")
        problems = _activation_basics(scheme, BandScheme, "Band scheme")
        if not scheme.bands.exists():
            problems.append("Add the bands.")
        elif not scheme.bands.filter(min_points=0).exists():
            problems.append("One band must start at 0 so that every total has a band.")
        if problems:
            raise FieldValidationError(
                "This band scheme cannot be activated yet.", fields={"activation": problems}
            )
        scheme.status = ACTIVE
        scheme.activated_by = actor
        scheme.activated_at = timezone.now()
        scheme.save(update_fields=["status", "activated_by", "activated_at"])
        _audit("band_scheme_activated", "kpi_band_scheme", scheme.pk, actor,
               old={"status": DRAFT}, new=_scheme_snapshot(scheme))
    return scheme


def retire_band_scheme(*, actor, scheme, last_day: date, reason: str) -> BandScheme:
    with transaction.atomic():
        scheme = BandScheme.objects.select_for_update().get(pk=scheme.pk)
        in_use = KPIWeightVersion.objects.filter(status=ACTIVE, band_scheme=scheme)
        return _retire(scheme, actor=actor, last_day=last_day, reason=reason, in_use=in_use,
                       action="band_scheme_retired", entity="kpi_band_scheme")


# --- plan defaults (department + system role) ----------------------------------------------


def system_role(name: str) -> Group:
    """The canonical system role: one of the four auth Groups (apps.accounts.roles)."""
    if name not in roles.ROLE_NAMES:
        raise FieldValidationError(
            fields={"role": [f"Use one of: {', '.join(roles.ROLE_NAMES)}."]}
        )
    return Group.objects.get(name=name)


def _kra_family(configuration: str) -> str:
    configuration = _code(configuration, "configuration")
    models_in_family = set(
        KPIWeightVersion.objects.filter(configuration=configuration).values_list(
            "calculation_model", flat=True
        )
    )
    if not models_in_family:
        raise FieldValidationError(fields={"configuration": ["No KRA plan has this code."]})
    if models_in_family != {KRA}:
        raise FieldValidationError(
            fields={"configuration": ["A legacy configuration cannot be a plan default."]}
        )
    return configuration


def _default_snapshot(row) -> dict:
    return {
        "department_id": row.department_id,
        "role": row.role.name,
        "configuration": row.configuration,
        "effective_from": _s(row.effective_from),
        "effective_to": _s(row.effective_to),
    }


def _refuse_finalized_kra_months(department, role, start: date, end: date | None) -> None:
    finalized = _months_overlapping(
        MonthlyPerformance.objects.filter(
            status=PerformanceStatus.FINALIZED, calculation_model=KRA,
            department=department, role_name=role.name,
        ),
        start, end,
    )
    if finalized.exists():
        raise ConfigConflict("This would change months that are already finalized.")


@_conflict_on_race
def create_plan_default(
    *, actor, department, role: str, configuration: str, effective_from: date,
    effective_to=None,
) -> KPIPlanDefault:
    group = system_role(role)
    configuration = _kra_family(configuration)
    _period(effective_from, effective_to)
    with transaction.atomic():
        current = KPIPlanDefault.objects.select_for_update().filter(
            department=department, role=group
        )
        if _overlaps(current, effective_from, effective_to).exists():
            raise ConfigConflict(
                "This department and role already have a plan default in this period."
            )
        _refuse_finalized_kra_months(department, group, effective_from, effective_to)
        row = KPIPlanDefault.objects.create(
            department=department, role=group, configuration=configuration,
            effective_from=effective_from, effective_to=effective_to, created_by=actor,
        )
        _audit("default_created", "kpi_plan_default", row.pk, actor, new=_default_snapshot(row))
    return row


def end_plan_default(*, actor, default, last_day: date) -> KPIPlanDefault:
    with transaction.atomic():
        row = KPIPlanDefault.objects.select_for_update().select_related("role").get(pk=default.pk)
        if last_day < row.effective_from:
            raise FieldValidationError(fields={"last_day": ["Cannot end before it starts."]})
        if row.effective_to is not None and last_day >= row.effective_to:
            raise ConfigConflict("This default already ends on or before that day.")
        _refuse_finalized_kra_months(row.department, row.role, last_day + ONE_DAY,
                                     row.effective_to)
        old = {"effective_to": _s(row.effective_to)}
        row.effective_to = last_day
        row.ended_by = actor
        row.ended_at = timezone.now()
        row.save(update_fields=["effective_to", "ended_by", "ended_at"])
        _audit("default_ended", "kpi_plan_default", row.pk, actor, old=old,
               new={"effective_to": last_day.isoformat()})
    return row


# --- employee overrides (EmployeeKPIAssignment with a reason) -------------------------------


def _refuse_finalized_employee_months(employee, start: date, end: date | None) -> None:
    finalized = _months_overlapping(
        MonthlyPerformance.objects.filter(employee=employee, status=PerformanceStatus.FINALIZED),
        start, end,
    )
    if finalized.exists():
        raise ConfigConflict("This would change months that are already finalized.")


@_conflict_on_race
def create_override(
    *, actor, employee, version, effective_from: date, effective_to=None, reason: str,
) -> EmployeeKPIAssignment:
    """HR override: this ACTIVE version applies to the employee for the period, whatever the
    department + role default says."""
    reason = _text(reason, "reason")
    _period(effective_from, effective_to)
    with transaction.atomic():
        version = _lock_version(version)
        _require_kra(version)  # legacy versions keep their own (legacy) assignment service
        if version.status != ACTIVE:
            raise ConfigConflict("Only an active plan version can be assigned.")
        if effective_from < version.effective_from:
            raise FieldValidationError(
                fields={"effective_from": ["Cannot start before the version takes effect."]}
            )
        if version.effective_to is not None and (
            effective_to is None or effective_to > version.effective_to
        ):
            raise FieldValidationError(
                fields={"effective_to": ["Must end by the version's last day."]}
            )
        current = EmployeeKPIAssignment.objects.select_for_update().filter(employee=employee)
        if _overlaps(current, effective_from, effective_to).exists():
            raise ConfigConflict("The employee already has an override in this period.")
        _refuse_finalized_employee_months(employee, effective_from, effective_to)
        row = EmployeeKPIAssignment.objects.create(
            employee=employee, weight_version=version, effective_from=effective_from,
            effective_to=effective_to, assigned_by=actor, reason=reason,
        )
        _audit("override_created", "employee", employee.pk, actor,
               new={"assignment_id": row.pk, "weight_version_id": version.pk,
                    "effective_from": effective_from.isoformat(),
                    "effective_to": _s(effective_to), "reason": reason})
    return row


def end_override(*, actor, assignment, last_day: date, reason: str = "") -> EmployeeKPIAssignment:
    with transaction.atomic():
        row = EmployeeKPIAssignment.objects.select_for_update().get(pk=assignment.pk)
        if last_day < row.effective_from:
            raise FieldValidationError(fields={"last_day": ["Cannot end before it starts."]})
        if row.effective_to is not None and last_day >= row.effective_to:
            raise ConfigConflict("This override already ends on or before that day.")
        _refuse_finalized_employee_months(row.employee, last_day + ONE_DAY, row.effective_to)
        old = {"effective_to": _s(row.effective_to)}
        row.effective_to = last_day
        row.save(update_fields=["effective_to"])
        _audit("override_ended", "employee", row.employee_id, actor, old=old,
               new={"assignment_id": row.pk, "effective_to": last_day.isoformat()},
               extra={"reason": (reason or "").strip()} if (reason or "").strip() else None)
    return row


# --- plan resolution ---------------------------------------------------------------------------


class ResolutionState:
    RESOLVED = "RESOLVED"
    NO_PLAN = "NO_PLAN"
    AMBIGUOUS_ROLE = "AMBIGUOUS_ROLE"


@dataclass
class PlanResolution:
    state: str
    source: str | None = None  # OVERRIDE / DEFAULT
    reason: str = ""
    version: KPIWeightVersion | None = None
    override: EmployeeKPIAssignment | None = None
    default: KPIPlanDefault | None = None
    roles: list[str] = field(default_factory=list)
    matching_defaults: list[KPIPlanDefault] = field(default_factory=list)


def resolve_plan(employee, day: date) -> PlanResolution:
    """Which plan applies to the employee on the day, and why. Read-only."""
    login_roles = (
        sorted(employee.user.groups.values_list("name", flat=True)) if employee.user_id else []
    )
    override = (
        EmployeeKPIAssignment.objects.select_related("weight_version")
        .filter(employee=employee, effective_from__lte=day)
        .exclude(effective_to__lt=day)
        .first()
    )
    if override is not None:
        version = override.weight_version
        if version.status == ACTIVE and _covers(version, day):
            return PlanResolution(ResolutionState.RESOLVED, "OVERRIDE", "HR override.",
                                  version=version, override=override, roles=login_roles)
        return PlanResolution(
            ResolutionState.NO_PLAN, "OVERRIDE",
            f"The HR override points at {version}, which is not active on this day.",
            override=override, roles=login_roles,
        )
    if not employee.user_id:
        return PlanResolution(ResolutionState.NO_PLAN, None,
                              "The employee has no login, so no system role; set an override.")
    defaults = list(
        KPIPlanDefault.objects.select_related("role", "department")
        .filter(department_id=employee.department_id, role__name__in=login_roles,
                effective_from__lte=day)
        .exclude(effective_to__lt=day)
        .order_by("role__name")
    )
    if not defaults:
        return PlanResolution(
            ResolutionState.NO_PLAN, None,
            "No plan default for this department and the employee's system role(s).",
            roles=login_roles,
        )
    if len(defaults) > 1:
        return PlanResolution(
            ResolutionState.AMBIGUOUS_ROLE, None,
            "Several of the employee's system roles have a plan default; HR must set an override.",
            roles=login_roles, matching_defaults=defaults,
        )
    default = defaults[0]
    version = (
        KPIWeightVersion.objects.filter(configuration=default.configuration, status=ACTIVE,
                                        calculation_model=KRA, effective_from__lte=day)
        .exclude(effective_to__lt=day)
        .first()
    )
    if version is None:
        return PlanResolution(
            ResolutionState.NO_PLAN, "DEFAULT",
            f"No ACTIVE version of {default.configuration} covers this day.",
            default=default, roles=login_roles, matching_defaults=defaults,
        )
    return PlanResolution(ResolutionState.RESOLVED, "DEFAULT", "Department + system role default.",
                          version=version, default=default, roles=login_roles,
                          matching_defaults=defaults)
