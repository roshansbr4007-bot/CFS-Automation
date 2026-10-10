"""Mandatory work response: comment kind.

Additive only. Adds tasks_comment.kind (varchar(14), NOT NULL) with the database default
'COMMENT' applied while the column is added, so every existing comment becomes an ordinary
COMMENT; the default is then dropped (Django supplies it on insert). No row is deleted and no
existing value is changed. Tasks completed before this migration simply have no WORK_RESPONSE
comment (their work_response reads as null). Reversible: rolling back drops the column; the
work-response rows are kept as plain comments (their kind is lost), and re-applying marks
every row COMMENT.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0012_reconciliation_prerequisite"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskcomment",
            name="kind",
            field=models.CharField(
                choices=[
                    ("COMMENT", "Comment"),
                    ("WORK_RESPONSE", "Work response (submitted on completion)"),
                ],
                default="COMMENT",
                max_length=14,
            ),
        ),
    ]
