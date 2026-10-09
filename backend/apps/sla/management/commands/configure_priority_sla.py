"""`python manage.py configure_priority_sla [--dry-run] [--force]`

Change Set 1 (D2/D3): configures the priority-based resolution SLA through the EXISTING Phase 5.2
infrastructure (versioned SlaRule + PrioritySla), using the existing services create_rule() and
set_priority_rule() (validated and audited). Run it explicitly, once per environment.

- Creates each missing rule (an existing rule code is never changed).
- Creates each missing priority mapping; an existing mapping is kept unless --force.
- Idempotent: running it again changes nothing.
- Never touches TaskSla: existing clocks keep their rule snapshot; only tasks raised afterwards
  use the mapping.

This module is the single place where the priority durations are defined.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.sla import services
from apps.sla.models import PrioritySla, SlaRule

SYSTEM_ACTOR_EMAIL = "scheduler@cfs.system"  # the existing system identity (Phase 5)

# priority (internal value), rule code, rule name, duration in minutes. URGENT is shown as
# "Critical" in the UI (D1); the stored value stays URGENT.
PRIORITY_SLA = (
    ("URGENT", "PRIORITY_URGENT_8H", "Critical priority — 8 hours", 8 * 60),
    ("HIGH", "PRIORITY_HIGH_24H", "High priority — 24 hours", 24 * 60),
    ("MEDIUM", "PRIORITY_MEDIUM_48H", "Medium priority — 48 hours", 48 * 60),
    ("LOW", "PRIORITY_LOW_72H", "Low priority — 72 hours", 72 * 60),
)


class Command(BaseCommand):
    help = "Create the priority SLA rules and priority mappings (idempotent; see --force)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only; change nothing.")
        parser.add_argument(
            "--force", action="store_true",
            help="Also replace an existing priority mapping that points to another rule.",
        )

    def handle(self, *args, dry_run=False, force=False, **options):
        actor = get_user_model().objects.filter(email=SYSTEM_ACTOR_EMAIL).first()
        if actor is None:
            raise CommandError(f"The system user {SYSTEM_ACTOR_EMAIL} does not exist.")
        prefix = "[dry run] " if dry_run else ""
        changes = 0
        with transaction.atomic():
            for priority, code, name, minutes in PRIORITY_SLA:
                rule = services.active_rule(code)
                if SlaRule.objects.filter(code=code).exists():
                    if rule is None:
                        self.stdout.write(f"{prefix}rule {code}: exists but has no active version "
                                          "— not changed; mapping skipped")
                        continue
                    self.stdout.write(f"{prefix}rule {code}: exists (kept)")
                    if rule.duration_minutes != minutes:
                        self.stdout.write(f"  warning: {code} lasts {rule.duration_minutes} "
                                          f"minutes, not {minutes}; it is not changed")
                else:
                    changes += 1
                    self.stdout.write(f"{prefix}rule {code}: created ({minutes} minutes)")
                    if not dry_run:
                        services.create_rule(
                            actor=actor, code=code, name=name, duration_minutes=minutes
                        )
                changes += self._map(actor, priority, code, dry_run, force, prefix)
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(f"{prefix}{changes} change(s).")

    def _map(self, actor, priority, code, dry_run, force, prefix) -> int:
        mapping = PrioritySla.objects.filter(priority=priority).first()
        if mapping is not None and mapping.rule_code == code and mapping.is_active:
            self.stdout.write(f"{prefix}mapping {priority}: already {code}")
            return 0
        if mapping is not None and not force:
            state = "active" if mapping.is_active else "inactive"
            self.stdout.write(f"{prefix}mapping {priority}: kept existing {mapping.rule_code} "
                              f"({state}); use --force to replace it")
            return 0
        verb = "replaced" if mapping is not None else "created"
        self.stdout.write(f"{prefix}mapping {priority}: {verb} -> {code}")
        if not dry_run:
            services.set_priority_rule(actor=actor, priority=priority, rule_code=code)
        return 1
