from apps.core.permissions import require_perm

CanManageUsers = require_perm("accounts.manage_users")
