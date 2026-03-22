"""add_unique_constraints_and_fk_cascade

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-22

Adds missing UniqueConstraint("compartment_id", "name") to pods, image_units,
kubes, and artifacts tables.  Adds ForeignKey cascade on
container_restart_stats.compartment_id so rows are cleaned up when a
compartment is deleted.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite does not support ALTER TABLE ADD CONSTRAINT, so we use
    # batch_alter_table which recreates the table behind the scenes.

    # -- pods: add unique(compartment_id, name) --
    with op.batch_alter_table("pods") as batch_op:
        batch_op.create_unique_constraint("uq_pods_compartment_name", ["compartment_id", "name"])

    # -- image_units: add unique(compartment_id, name) --
    with op.batch_alter_table("image_units") as batch_op:
        batch_op.create_unique_constraint(
            "uq_image_units_compartment_name", ["compartment_id", "name"]
        )

    # -- kubes: add unique(compartment_id, name) --
    with op.batch_alter_table("kubes") as batch_op:
        batch_op.create_unique_constraint("uq_kubes_compartment_name", ["compartment_id", "name"])

    # -- artifacts: add unique(compartment_id, name) --
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.create_unique_constraint(
            "uq_artifacts_compartment_name", ["compartment_id", "name"]
        )

    # -- container_restart_stats: add FK cascade on compartment_id --
    with op.batch_alter_table("container_restart_stats") as batch_op:
        batch_op.create_foreign_key(
            "fk_restart_stats_compartment",
            "compartments",
            ["compartment_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("container_restart_stats") as batch_op:
        batch_op.drop_constraint("fk_restart_stats_compartment", type_="foreignkey")

    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_constraint("uq_artifacts_compartment_name", type_="unique")

    with op.batch_alter_table("kubes") as batch_op:
        batch_op.drop_constraint("uq_kubes_compartment_name", type_="unique")

    with op.batch_alter_table("image_units") as batch_op:
        batch_op.drop_constraint("uq_image_units_compartment_name", type_="unique")

    with op.batch_alter_table("pods") as batch_op:
        batch_op.drop_constraint("uq_pods_compartment_name", type_="unique")
