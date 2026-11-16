"""S116-B1 — Organizations helpers (payer vs member, names, OU paths, fallback)."""

from __future__ import annotations

from botocore.exceptions import ClientError

from sofe.billing.organization import build_organization, describe_organization, list_accounts

PAYER = "111111111111"
MEMBER = "222222222222"


class FakeOrg:
    def __init__(self, describe_error: Exception | None = None):
        self.describe_error = describe_error
        self.calls: list[tuple[str, dict]] = []

    def describe_organization(self):
        self.calls.append(("describe_organization", {}))
        if self.describe_error:
            raise self.describe_error
        return {
            "Organization": {
                "Id": "o-example123",
                "MasterAccountId": PAYER,
                "FeatureSet": "ALL",
            }
        }

    def list_accounts(self, **kwargs):
        self.calls.append(("list_accounts", kwargs))
        if kwargs.get("NextToken") == "page-2":
            return {
                "Accounts": [
                    {
                        "Id": MEMBER,
                        "Name": "Data Platform",
                        "Email": "data@example.com",
                        "Status": "ACTIVE",
                        "JoinedMethod": "INVITED",
                    }
                ]
            }
        return {
            "Accounts": [
                {
                    "Id": PAYER,
                    "Name": "Production",
                    "Email": "prod@example.com",
                    "Status": "ACTIVE",
                    "JoinedMethod": "CREATED",
                }
            ],
            "NextToken": "page-2",
        }

    def list_parents(self, ChildId):
        self.calls.append(("list_parents", {"ChildId": ChildId}))
        if ChildId == MEMBER:
            return {"Parents": [{"Id": "ou-example-111", "Type": "ORGANIZATIONAL_UNIT"}]}
        if ChildId == "ou-example-111":
            return {"Parents": [{"Id": "r-example", "Type": "ROOT"}]}
        return {"Parents": [{"Id": "r-example", "Type": "ROOT"}]}

    def describe_organizational_unit(self, OrganizationalUnitId):
        self.calls.append(("describe_organizational_unit", {"OrganizationalUnitId": OrganizationalUnitId}))
        return {
            "OrganizationalUnit": {
                "Id": OrganizationalUnitId,
                "Name": "Engineering",
            }
        }


class FakeIAM:
    def list_account_aliases(self):
        return {"AccountAliases": ["prod-alias"]}


def client_error(code: str = "AccessDeniedException") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "not authorized"}}, "DescribeOrganization")


def test_list_accounts_follows_pagination():
    org = FakeOrg()
    accounts = list_accounts(org)
    assert [account.id for account in accounts] == [PAYER, MEMBER]
    assert [call[1].get("NextToken") for call in org.calls if call[0] == "list_accounts"] == [None, "page-2"]


def test_payer_view_has_names_ou_and_is_payer():
    result = build_organization(FakeOrg(), PAYER, iam=FakeIAM())

    assert result.available is True
    assert result.is_payer is True
    assert result.management_account_id == PAYER
    assert [account.id for account in result.accounts] == [PAYER, MEMBER]
    by_id = {account.id: account for account in result.accounts}
    assert by_id[PAYER].is_payer is True
    assert by_id[MEMBER].is_payer is False
    assert by_id[MEMBER].name == "Data Platform"
    assert by_id[MEMBER].ou_id == "ou-example-111"
    assert by_id[MEMBER].ou_path == "Engineering"
    assert by_id[PAYER].alias == "prod-alias"


def test_member_view_is_not_a_payer():
    result = build_organization(FakeOrg(), MEMBER)
    assert result.available is True
    assert result.is_payer is False
    assert result.management_account_id == PAYER


def test_organizations_access_denied_is_a_normal_fallback():
    result = build_organization(FakeOrg(describe_error=client_error()), PAYER)
    assert result.available is False
    assert result.accounts == []
    assert result.is_payer is False
    assert result.detail == "AccessDeniedException"


def test_describe_organization_accepts_new_and_legacy_management_fields():
    class NewOrg(FakeOrg):
        def describe_organization(self):
            return {"Organization": {"Id": "o-x", "ManagementAccountId": PAYER}}

    available, management, detail = describe_organization(NewOrg())
    assert (available, management, detail) == (True, PAYER, "")
