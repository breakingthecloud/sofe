"""IAM gap checker — S111 Phase C.

It exists because IAM accepts a policy that names an action that does not exist: it just
never matches. v2 of the customer role listed three misnamed S3 actions and nobody could tell.
"""

import importlib.util
import pathlib
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("iam_gap_check", ROOT / "scripts" / "iam_gap_check.py")
gap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gap)

PLATFORM_TEMPLATE = ROOT.parent / "sofe-platform" / "public" / "cfn" / "sofe-read-only-role-v2.yaml"


def template(tmp_path, allow=(), deny=()):
    lines = ["Resources:", "  R:", "    Type: AWS::IAM::Role", "    Properties:", "      Policies:", "        - PolicyName: p",
             "          PolicyDocument:", "            Statement:"]
    if allow:
        lines += ["              - Effect: Allow", "                Action:"] + [f"                  - '{a}'" for a in allow] + ["                Resource: '*'"]
    if deny:
        lines += ["              - Effect: Deny", "                Action:"] + [f"                  - '{a}'" for a in deny] + ["                Resource: '*'"]
    p = tmp_path / "t.yaml"
    p.write_text("\n".join(lines) + "\n")
    return p


class TestRequiredActions:
    @pytest.fixture(scope="class")
    def needed(self):
        return gap.required_actions()

    def test_s3_actions_use_the_names_iam_authorizes_not_the_api_names(self, needed):
        assert {"s3:ListAllMyBuckets", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetBucketPublicAccessBlock"} <= set(needed)
        for wrong in ("s3:ListBuckets", "s3:GetBucketEncryption", "s3:GetBucketLifecycleConfiguration", "s3:GetPublicAccessBlock"):
            assert wrong not in needed

    def test_api_gateway_reads_need_the_single_get_action(self, needed):
        assert "apigateway:GET" in needed
        assert not [a for a in needed if a.startswith("apigateway:") and a != "apigateway:GET"]

    def test_load_balancers_use_the_elasticloadbalancing_prefix(self, needed):
        assert "elasticloadbalancing:DescribeLoadBalancers" in needed
        assert not [a for a in needed if a.startswith("elbv2:")]

    def test_calls_that_need_no_permission_or_are_old_sdk_fallbacks_are_not_required(self, needed):
        assert "sts:GetCallerIdentity" not in needed
        assert "s3:HeadBucket" not in needed and "s3:ListBucket" not in needed  # would expose object keys

    def test_every_source_is_reported(self, needed):
        assert needed["s3:GetEncryptionConfiguration"] == ["s3.py: s3.get_bucket_encryption"]

    def test_new_s3_checks_are_found_automatically(self, needed):
        assert "s3:GetBucketLogging" in needed and "s3:GetBucketTagging" in needed and "s3:GetBucketVersioning" in needed

    def test_manual_billing_api_actions_are_required(self, needed):
        assert "billing:GetCredits" in needed
        assert "billing:GetCreditAllocationHistory" in needed


class TestGaps:
    def test_a_missing_action_is_reported(self, tmp_path):
        stm = gap.template_statements(template(tmp_path, allow=["s3:ListAllMyBuckets"]))

        missing, denied = gap.gaps({"s3:ListAllMyBuckets": ["x"], "ec2:DescribeInstances": ["y"]}, stm)

        assert missing == ["ec2:DescribeInstances"] and denied == []

    def test_wildcards_and_case_are_honoured_like_iam_does(self, tmp_path):
        stm = gap.template_statements(template(tmp_path, allow=["EC2:describe*"]))

        assert gap.gaps({"ec2:DescribeVolumes": ["x"]}, stm) == ([], [])

    def test_an_explicit_deny_wins_over_an_allow(self, tmp_path):
        stm = gap.template_statements(template(tmp_path, allow=["s3:Get*"], deny=["s3:GetEncryptionConfiguration"]))

        missing, denied = gap.gaps({"s3:GetEncryptionConfiguration": ["x"], "s3:GetBucketTagging": ["y"]}, stm)

        assert missing == [] and denied == ["s3:GetEncryptionConfiguration"]

    def test_the_misnamed_v2_action_does_not_satisfy_the_real_one(self, tmp_path):
        stm = gap.template_statements(template(tmp_path, allow=["s3:GetBucketEncryption"]))

        assert gap.gaps({"s3:GetEncryptionConfiguration": ["x"]}, stm)[0] == ["s3:GetEncryptionConfiguration"]

    def test_cloudformation_intrinsic_tags_do_not_break_parsing(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(textwrap.dedent("""\
            Parameters: {ExternalId: {Type: String}}
            Resources:
              R:
                Type: AWS::IAM::Role
                Properties:
                  AssumeRolePolicyDocument:
                    Statement:
                      - Condition: {StringEquals: {'sts:ExternalId': !Ref ExternalId}}
                  Policies:
                    - PolicyName: p
                      PolicyDocument:
                        Statement:
                          - {Effect: Allow, Action: ['ec2:DescribeInstances'], Resource: '*'}
            """))

        assert gap.gaps({"ec2:DescribeInstances": ["x"]}, gap.template_statements(p)) == ([], [])


@pytest.mark.skipif(not PLATFORM_TEMPLATE.exists(), reason="sofe-platform checkout not next to sofe")
def test_the_published_customer_role_template_covers_every_action_the_engine_calls():
    needed = gap.required_actions()
    missing, denied = gap.gaps(needed, gap.template_statements(PLATFORM_TEMPLATE))

    assert missing == [] and denied == []
