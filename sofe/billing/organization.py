"""AWS Organizations helpers for Billing & Cost Explorer (S116-B1).

A payer/management account can see every member account; a member account can only
see itself. These helpers make that explicit instead of guessing from Cost Explorer
results. They never create sessions; the caller passes an Organizations client
(usually built from an assumed role).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from .models import OrganizationAccount, OrganizationResult

MAX_ACCOUNT_PAGES = 50
MAX_OU_DEPTH = 5


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "") or "ClientError")
    return type(exc).__name__


def describe_organization(org: Any) -> tuple[bool, str | None, str]:
    """Return ``(available, management_account_id, detail)``.

    ``available=False`` is a normal answer for a member account without
    Organizations permissions or for an account outside an organization.
    """
    try:
        response = org.describe_organization()
    except Exception as exc:  # noqa: BLE001 - normalize unavailable organizations
        return False, None, _error_code(exc)
    organization = response.get("Organization") or {}
    management = organization.get("MasterAccountId") or organization.get("ManagementAccountId")
    if not management:
        return False, None, "ManagementAccountIdMissing"
    return True, str(management), ""


def list_accounts(org: Any) -> list[OrganizationAccount]:
    """List all organization accounts, following ``NextToken`` with a hard cap."""
    accounts: list[OrganizationAccount] = []
    token: str | None = None
    for _ in range(MAX_ACCOUNT_PAGES):
        kwargs: dict[str, Any] = {}
        if token:
            kwargs["NextToken"] = token
        response = org.list_accounts(**kwargs)
        for account in response.get("Accounts", []) or []:
            account_id = account.get("Id")
            if not account_id:
                continue
            accounts.append(
                OrganizationAccount(
                    id=str(account_id),
                    name=str(account.get("Name", "") or ""),
                    email=str(account.get("Email", "") or ""),
                    status=str(account.get("Status", "") or ""),
                    joined_method=str(account.get("JoinedMethod", "") or ""),
                )
            )
        token = response.get("NextToken")
        if not token:
            break
    return accounts


def _ou_for_account(org: Any, account_id: str) -> tuple[str | None, str | None]:
    """Return the immediate OU id and a readable path (root omitted)."""
    try:
        parents = org.list_parents(ChildId=account_id).get("Parents", []) or []
    except Exception:  # noqa: BLE001 - OU labels are optional
        return None, None

    ou = next((parent for parent in parents if parent.get("Type") == "ORGANIZATIONAL_UNIT"), None)
    if not ou or not ou.get("Id"):
        return None, None

    ou_id = str(ou["Id"])
    names: list[str] = []
    current = ou_id
    for _ in range(MAX_OU_DEPTH):
        try:
            detail = org.describe_organizational_unit(OrganizationalUnitId=current).get("OrganizationalUnit", {})
        except Exception:  # noqa: BLE001 - keep the id even if the label is unavailable
            break
        name = str(detail.get("Name", "") or "")
        if name:
            names.append(name)
        try:
            parents = org.list_parents(ChildId=current).get("Parents", []) or []
        except Exception:  # noqa: BLE001
            break
        parent = next((item for item in parents if item.get("Type") == "ORGANIZATIONAL_UNIT"), None)
        if not parent or not parent.get("Id"):
            break
        current = str(parent["Id"])

    return ou_id, " / ".join(reversed(names)) if names else None


def _account_alias(iam: Any, account_id: str) -> str | None:
    try:
        aliases = iam.list_account_aliases().get("AccountAliases", []) or []
    except Exception:  # noqa: BLE001
        return None
    return str(aliases[0]) if aliases else None


def build_organization(
    org: Any,
    caller_account_id: str | None,
    *,
    iam: Any | None = None,
    include_ous: bool = True,
) -> OrganizationResult:
    """Build a normalized organization view.

    A member/non-org account returns ``available=False`` with a safe detail code;
    it must not pretend to be a payer.
    """
    available, management_account_id, detail = describe_organization(org)
    if not available:
        return OrganizationResult(available=False, detail=detail)

    try:
        accounts = list_accounts(org)
    except Exception as exc:  # noqa: BLE001
        return OrganizationResult(available=False, detail=_error_code(exc))

    is_payer = bool(caller_account_id and management_account_id == caller_account_id)
    if include_ous:
        for account in accounts:
            account.ou_id, account.ou_path = _ou_for_account(org, account.id)

    if iam is not None and caller_account_id:
        for account in accounts:
            if account.id == caller_account_id:
                account.alias = _account_alias(iam, account.id)
                break

    for account in accounts:
        account.is_payer = account.id == management_account_id

    return OrganizationResult(
        available=True,
        is_payer=is_payer,
        management_account_id=management_account_id,
        accounts=accounts,
        detail="",
    )
