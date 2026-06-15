"""Table registry for the PostgREST-style API.

Each exposed table declares its model, the columns visible to clients
(in the exact names the original Supabase API used), FK embeds, and a
row-level security policy mirroring the original RLS.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from django.db.models import Q

from apps.authentication.models import Profile, UserRole
from apps.catalog.models import AddOn, Carrier, LeadSource, Product
from apps.dbapi.roles import (
    current_ghl_user_ids,
    is_admin,
    is_manager,
    managed_team_ids,
)
from apps.expenses.models import Expense
from apps.ghl.models import GhlContact, GhlUser
from apps.sales.models import Sale
from apps.targets.models import Target
from apps.teams.models import Team, TeamManager

DENY = object()  # sentinel: no access


@dataclass
class Policy:
    """Q-object factories per operation. Return None for full access,
    DENY for no access, or a Q to scope rows."""

    select: Callable
    insert: Callable  # (user, row_dict) -> bool
    update: Callable
    delete: Callable


@dataclass
class TableConfig:
    model: type
    columns: dict  # exposed column name -> model attname
    policy: Policy
    embeds: dict = field(default_factory=dict)  # fk exposed col -> (model, accessor)


# --- policy building blocks -------------------------------------------------


def allow_all(user):
    return None


def deny(user):
    return DENY


def authenticated_only(user):
    return None if user.is_authenticated else DENY


def admin_only(user):
    return None if is_admin(user) else DENY


def admin_write(user, row=None):
    return is_admin(user)


def authenticated_write(user, row=None):
    return bool(user.is_authenticated)


# --- RLS-equivalent policies (final state of the original migrations) -------


def manager_team_q(user) -> Q | None:
    """has_role(manager) AND team_id IS NOT NULL AND is_team_manager(uid, team_id)"""
    if not is_manager(user):
        return None
    managed = managed_team_ids(user)
    if not managed:
        return None
    return Q(team_id__in=managed)


# profiles_select_scoped: own OR admin OR (manager AND is_team_manager(team_id))
def profiles_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    q = Q(pk=user.pk)
    mq = manager_team_q(user)
    if mq is not None:
        q |= mq
    return q


# profiles_update_own OR profiles_admin_all
def profiles_update(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    return Q(pk=user.pk)


# profiles INSERT: only via admin policy (signup happens server-side)
def profiles_insert(user, row):
    if not user.is_authenticated:
        return False
    if is_admin(user):
        return True
    return str(row.get("id")) == str(user.pk)


# roles_select_own: own OR admin
def user_roles_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    return Q(user_id=user.pk)


def user_roles_write(user, row=None):
    return is_admin(user)


def user_roles_update_q(user):
    return None if is_admin(user) else DENY


# sales_select_scoped: admin OR own OR (manager AND is_team_manager(team_id))
def sales_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    q = Q(agent_id=user.pk)
    mq = manager_team_q(user)
    if mq is not None:
        q |= mq
    return q


# sales_insert_self: WITH CHECK (agent_id = auth.uid()) — admins also pass via
# the absence of an admin-all policy? No: original has only insert_self, so
# even admins must insert with agent_id = their own uid... but admin UPDATE is
# allowed. The original sale form always sets agent_id = auth user, so behavior
# is identical either way; we enforce exactly the original WITH CHECK.
def sales_insert(user, row):
    if not user.is_authenticated:
        return False
    return str(row.get("agent_id")) == str(user.pk)


# sales_update_scoped: admin OR (manager AND is_team_manager) OR own
def sales_update(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    q = Q(agent_id=user.pk)
    mq = manager_team_q(user)
    if mq is not None:
        q |= mq
    return q


# sales_admin_delete: admin only
def sales_delete(user):
    return None if is_admin(user) else DENY


# expenses_select_own_or_privileged: own OR admin OR manager
def expenses_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user) or is_manager(user):
        return None
    return Q(agent_id=user.pk)


# expenses_insert_self: WITH CHECK (agent_id = auth.uid())
def expenses_insert(user, row):
    if not user.is_authenticated:
        return False
    return str(row.get("agent_id")) == str(user.pk)


# expenses_update_scoped / expenses_delete_scoped: admin OR manager OR own
def expenses_write(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user) or is_manager(user):
        return None
    return Q(agent_id=user.pk)


# targets_select_scoped: admin OR company scope OR own OR
# (manager AND target's agent profile team is managed by user)
def targets_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    q = Q(scope="company") | Q(agent_id=user.pk)
    if is_manager(user):
        managed = managed_team_ids(user)
        if managed:
            agent_ids = Profile.objects.filter(team_id__in=managed).values_list(
                "user_id", flat=True
            )
            q |= Q(agent_id__in=list(agent_ids))
    return q


# ghl_users_select_own: app_user_id = auth.uid() OR admin
def ghl_users_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    return Q(app_user_id=user.pk)


# ghl_contacts_select_own: user_id IN current_ghl_user_ids() OR admin
def ghl_contacts_select(user):
    if not user.is_authenticated:
        return DENY
    if is_admin(user):
        return None
    ids = current_ghl_user_ids(user)
    return Q(user_id__in=ids) if ids else Q(pk__in=[])


# --- registry ---------------------------------------------------------------

TABLES: dict[str, TableConfig] = {
    "profiles": TableConfig(
        model=Profile,
        columns={
            "id": "user_id",
            "display_name": "display_name",
            "email": "email",
            "phone": "phone",
            "team_id": "team_id",
            "must_change_password": "must_change_password",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        embeds={"team_id": ("teams", "team")},
        policy=Policy(
            select=profiles_select,
            insert=profiles_insert,
            update=profiles_update,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "user_roles": TableConfig(
        model=UserRole,
        columns={"id": "id", "user_id": "user_id", "role": "role"},
        policy=Policy(
            select=user_roles_select,
            insert=user_roles_write,
            update=user_roles_update_q,
            delete=user_roles_update_q,
        ),
    ),
    "teams": TableConfig(
        model=Team,
        columns={
            "id": "id",
            "name": "name",
            "manager_id": "manager_id",
            "created_at": "created_at",
        },
        policy=Policy(
            select=authenticated_only,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "team_managers": TableConfig(
        model=TeamManager,
        columns={
            "id": "id",
            "team_id": "team_id",
            "user_id": "user_id",
            "created_at": "created_at",
        },
        policy=Policy(
            select=authenticated_only,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "carriers": TableConfig(
        model=Carrier,
        columns={
            "id": "id",
            "name": "name",
            "carrier_type": "carrier_type",
            "active": "active",
            "created_at": "created_at",
        },
        policy=Policy(
            select=authenticated_only,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "products": TableConfig(
        model=Product,
        columns={
            "id": "id",
            "name": "name",
            "carrier_id": "carrier_id",
            "active": "active",
            "created_at": "created_at",
        },
        policy=Policy(
            select=authenticated_only,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "add_ons": TableConfig(
        model=AddOn,
        columns={
            "id": "id",
            "name": "name",
            "active": "active",
            "created_at": "created_at",
        },
        policy=Policy(
            select=authenticated_only,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "lead_sources": TableConfig(
        model=LeadSource,
        columns={
            "id": "id",
            "name": "name",
            "active": "active",
            "created_at": "created_at",
        },
        policy=Policy(
            select=authenticated_only,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "sales": TableConfig(
        model=Sale,
        columns={
            "id": "id",
            "sale_id": "sale_id",
            "agent_id": "agent_id",
            "agent_name": "agent_name",
            "team_id": "team_id",
            "team_name": "team_name",
            "sale_date": "sale_date",
            "customer_name": "customer_name",
            "deal_size": "deal_size",
            "carrier": "carrier",
            "product": "product",
            "add_ons": "add_ons",
            "add_on_amounts": "add_on_amounts",
            "line_items": "line_items",
            "lead_source": "lead_source",
            "cost_per_lead": "cost_per_lead",
            "notes": "notes",
            "created_at": "created_at",
        },
        policy=Policy(
            select=sales_select,
            insert=sales_insert,
            update=sales_update,
            delete=sales_delete,
        ),
    ),
    "expenses": TableConfig(
        model=Expense,
        columns={
            "id": "id",
            "agent_id": "agent_id",
            "amount": "amount",
            "start_date": "start_date",
            "end_date": "end_date",
            "notes": "notes",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        policy=Policy(
            select=expenses_select,
            insert=expenses_insert,
            update=expenses_write,
            delete=expenses_write,
        ),
    ),
    "targets": TableConfig(
        model=Target,
        columns={
            "id": "id",
            "scope": "scope",
            "agent_id": "agent_id",
            "life_revenue_target": "life_revenue_target",
            "health_revenue_target": "health_revenue_target",
            "addon_revenue_target": "addon_revenue_target",
            "life_attach_ratio_target": "life_attach_ratio_target",
            "health_attach_ratio_target": "health_attach_ratio_target",
            "addon_attach_ratio_target": "addon_attach_ratio_target",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        policy=Policy(
            select=targets_select,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "ghl_users": TableConfig(
        model=GhlUser,
        columns={
            "id": "id",
            "app_user_id": "app_user_id",
            "location_id": "location_id",
            "name": "name",
            "email": "email",
            "phone": "phone",
            "type": "type",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        policy=Policy(
            select=ghl_users_select,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
    "ghl_contacts": TableConfig(
        model=GhlContact,
        columns={
            "id": "id",
            "user_id": "user_id",
            "location_id": "location_id",
            "name": "name",
            "email": "email",
            "phone": "phone",
            "type": "type",
            "created_at": "created_at",
            "updated_at": "updated_at",
        },
        policy=Policy(
            select=ghl_contacts_select,
            insert=admin_write,
            update=lambda user: None if is_admin(user) else DENY,
            delete=lambda user: None if is_admin(user) else DENY,
        ),
    ),
}
