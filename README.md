# AWS Security Agent + GitLab CI/CD POC

A minimal, isolated proof-of-concept showing:

**Git push → build → CloudFormation deploy to pre-prod → AWS Security Agent scoped penetration test → gate → manual production promotion**

The application intentionally contains a SQL injection vulnerability so the first run should produce a `HIGH` finding and block production. After fixing the query with a parameterized statement, the next run should pass.

> **Safety:** Deploy this only to an isolated AWS account/environment. Do not connect it to production data or expose it as a real application. The vulnerability is intentional.

## Table of contents

- [Architecture](#architecture)
- [Repository](#repository)
- [1. Prerequisites](#1-prerequisites)
- [2. Create GitLab OIDC provider](#2-create-gitlab-oidc-provider)
- [3. Bootstrap the artifact bucket](#3-bootstrap-the-artifact-bucket)
- [4. Create GitLab IAM roles](#4-create-gitlab-iam-roles)
- [5. Create the first application stack manually](#5-create-the-first-application-stack-manually)
- [6. Configure AWS Security Agent](#6-configure-aws-security-agent)
- [7. Configure GitLab variables](#7-configure-gitlab-variables)
- [8. GitLab pipeline](#8-gitlab-pipeline)
  - [First run](#first-run)
  - [Expected vulnerable result](#expected-vulnerable-result)
  - [Fix and prove the gate passes](#fix-and-prove-the-gate-passes)
- [9. Important security controls](#9-important-security-controls)
- [Official references](#official-references)

## Architecture

```text
                         GitLab
                            |
                         push main
                            |
                     +------+------+
                     |             |
                   Build       Package app
                                   |
                                   v
                         S3 artifact bucket
                                   |
                                   v
                         CloudFormation
                                   |
                         +---------+---------+
                         |                   |
                    PRE-PROD stack       PROD stack
                         |                   ^
                         v                   |
                Route 53 DNS name            |
                         |                   |
                  API Gateway HTTP API       |
                         |                   |
                       Lambda                |
                         |                   |
                         +---- AWS Security Agent
                               scoped pentest
                                      |
                         +------------+------------+
                         |                         |
                     HIGH finding              PASS
                         |                         |
                     pipeline FAIL          manual PROD
                         |                         |
                       blocked                  promote
```

AWS Security Agent's GitLab integration is a **post-deployment** gate. It tests the deployed change rather than doing a pull-request-only source scan. The integration is currently public preview and can change. See the official AWS tutorial before productionizing this POC.

## Repository

```text
aws-security-agent-poc/
├── README.md
├── .gitignore
├── .gitlab-ci.yml
├── cloudformation/
│   ├── 00-artifact-bucket.yaml
│   ├── 01-application.yaml
│   └── 02-gitlab-security-agent-iam.yaml
├── app/
│   └── index.py
└── scripts/
    ├── package.sh
    ├── deploy-application.sh
    └── destroy.sh
```

## 1. Prerequisites

You need:

- AWS account with permissions to create the POC resources.
- GitLab project on GitLab.com.
- AWS CLI configured locally for the one-time bootstrap.
- A Route 53 public hosted zone, for example `example.com`.
- An AWS Region supported by AWS Security Agent, and the Agent Space / penetration test must be in that Region.
- A GitLab Project Access Token with `read_api` and `write_repository`, Developer role or higher.
- GitLab CI/CD project maximum job timeout greater than the gate job timeout.

AWS recommends GitLab OIDC rather than long-lived AWS access keys.

## 2. Create GitLab OIDC provider

Do this once per AWS account if you do not already have the provider:

```bash
aws iam create-open-id-connect-provider   --url https://gitlab.com   --client-id-list https://gitlab.com
```

If the provider already exists, do not create it again.

Get the provider ARN:

```bash
aws iam list-open-id-connect-providers
```

It should look like:

```text
arn:aws:iam::<ACCOUNT_ID>:oidc-provider/gitlab.com
```

This approach deliberately avoids embedding a hard-coded certificate thumbprint in CloudFormation.

## 3. Bootstrap the artifact bucket

The application package is uploaded to S3 and CloudFormation points Lambda at that immutable artifact.

```bash
aws cloudformation deploy   --template-file cloudformation/00-artifact-bucket.yaml   --stack-name security-agent-poc-artifacts
```

Get the bucket name:

```bash
aws cloudformation describe-stacks   --stack-name security-agent-poc-artifacts   --query 'Stacks[0].Outputs[?OutputKey==`ArtifactBucketName`].OutputValue'   --output text
```

## 4. Create GitLab IAM roles

First get your account ID:

```bash
aws sts get-caller-identity --query Account --output text
```

Then deploy:

```bash
aws cloudformation deploy   --template-file cloudformation/02-gitlab-security-agent-iam.yaml   --stack-name security-agent-poc-gitlab-iam   --parameter-overrides     GitLabProjectPath=<YOUR_GROUP>/<YOUR_PROJECT>     GitLabBranch=main     GitLabOIDCProviderArn=arn:aws:iam::<ACCOUNT_ID>:oidc-provider/gitlab.com     ArtifactBucketName=<ARTIFACT_BUCKET_NAME>   --capabilities CAPABILITY_NAMED_IAM
```

Get the role ARNs:

```bash
aws cloudformation describe-stacks   --stack-name security-agent-poc-gitlab-iam   --query 'Stacks[0].Outputs'
```

The stack creates:

- `GitLabDeploymentRole`
- `GitLabSecurityAgentRole`

The Security Agent role is restricted to the GitLab project and `main` branch in its trust policy.

The deployment role is intentionally broader than a production-grade deployment role because this is a small POC. Tighten it before reuse.

## 5. Create the first application stack manually

Package the Lambda:

```bash
chmod +x scripts/*.sh
./scripts/package.sh
```

Upload it:

```bash
ARTIFACT_BUCKET=$(aws cloudformation describe-stacks   --stack-name security-agent-poc-artifacts   --query 'Stacks[0].Outputs[?OutputKey==`ArtifactBucketName`].OutputValue'   --output text)

aws s3 cp build/app.zip "s3://${ARTIFACT_BUCKET}/releases/initial/app.zip"
```

Deploy pre-prod:

```bash
aws cloudformation deploy   --template-file cloudformation/01-application.yaml   --stack-name security-agent-poc-preprod   --parameter-overrides     ArtifactBucketName="$ARTIFACT_BUCKET"     ArtifactKey=releases/initial/app.zip     HostedZoneId=<ROUTE53_HOSTED_ZONE_ID>     DomainName=pentest-preprod.example.com     Environment=preprod   --capabilities CAPABILITY_NAMED_IAM
```

Wait for the ACM certificate to validate and the custom domain to become available.

Test:

```bash
curl "https://pentest-preprod.example.com/products?category=electronics"
```

Try the intentionally vulnerable input:

```bash
curl "https://pentest-preprod.example.com/products?category=electronics%27%20OR%20%271%27%3D%271"
```

Do not do this against anything except this isolated POC.

## 6. Configure AWS Security Agent

In AWS Security Agent:

1. Create an Agent Space (**Set up AWS Security Agent**, user access: **IAM-only access**).
2. In the Agent Space, **Penetration test** tab → **Set up penetration test**. Add `pentest-preprod.example.com` and verify it. Because the hosted zone is in the same AWS account, select the domain and choose **One-click verification**. Keep the default service role that AWS Security Agent creates.
3. Connect the GitLab repository as the source-control integration (**Connect a source code provider for penetration testing**).
4. In the web application (**Web app** tab → **Admin access**), create a penetration test:
   - Test scope: **Web application**
   - Target URL: `https://pentest-preprod.example.com`
   - Credentials: none
   - VPC: none (the target is public)
   - Network configuration: no accessible domains; optionally mark paths such as `/admin` as **Out of scope**
   - Additional configuration: add the GitLab repository for application context
5. Enable CI/CD mode on the penetration test.
6. Edit the penetration test → **Continuous testing** → **Max task hours per run**: `2` (the minimum), or leave empty for no limit.
7. Note:
   - Agent Space ID: `as-...`
   - Penetration test ID: `pt-...`

Run one manual pentest first. This proves that the application, DNS, domain verification, scope and Security Agent setup work independently of GitLab.

For the first CI/CD validation, use `dry-run: "true"`.

## 7. Configure GitLab variables

In **Settings → CI/CD → Variables**, add:

| Variable | Value |
|---|---|
| `AWS_REGION` | Region containing the Agent Space |
| `HOSTED_ZONE_ID` | Route 53 hosted zone ID |
| `ARTIFACT_BUCKET` | Artifact bucket output |
| `DEPLOYMENT_ROLE_ARN` | GitLab deployment role output |
| `SECURITY_AGENT_ROLE_ARN` | Security Agent role output |
| `AGENT_SPACE_ID` | `as-...` |
| `PENTEST_ID` | `pt-...` |
| `SA_GITLAB_TOKEN` | Project/Group Access Token with `read_api`, `write_repository` |

Mark sensitive values masked/protected where GitLab allows.

Also in GitLab:

- Edit `PREPROD_DOMAIN_NAME` and `PROD_DOMAIN_NAME` at the top of `.gitlab-ci.yml` to hostnames in your hosted zone. The pre-prod name must be the domain verified in AWS Security Agent.
- **Settings → CI/CD → General pipelines**: set the project maximum job timeout above the gate's `job-timeout` (55 minutes), for example 1 hour.
- **Settings → CI/CD → Variables**: set the minimum role allowed to override pipeline variables. Otherwise anyone who can run a pipeline can lower the severity threshold or turn on dry-run.
- **Settings → Repository → Protected tags**: leave `continuum/pentest/cicd/baseline/*` unprotected (the default) so `SA_GITLAB_TOKEN` can create baseline tags.

## 8. GitLab pipeline

The included `.gitlab-ci.yml` does:

1. Validate CloudFormation.
2. Package the Lambda.
3. Assume the deployment role using GitLab OIDC.
4. Upload the application artifact.
5. Deploy pre-prod with CloudFormation.
6. Run the AWS Security Agent scoped pentest.
7. Block production on `HIGH` or higher.
8. Make production promotion manual only after the gate passes.

The gate job is named `aws-continuum-pentest`, which is the job the production job depends on.

### First run

The repository starts with:

```yaml
dry-run: "true"
```

Run the pipeline and verify authentication, Region, GitLab source-control integration and configuration. The dry run does not start a pentest, so the gate passes and `deploy-prod` becomes available; do not promote from this run.

Then remove the `dry-run` input and push again.

The gate also sets `fail-on-error: "true"`. The integration's shipped default is fail-open, which would let an infrastructure error pass the gate without testing anything.

### Expected vulnerable result

The vulnerable version should produce a result similar to:

```text
Security Agent
  Scope decision: IN_SCOPE
  Finding: SQL injection
  Severity: HIGH
  Gate: BLOCK
```

Therefore:

```text
deploy-preprod      PASS
aws-continuum-pentest FAIL
deploy-prod         BLOCKED
```

### Fix and prove the gate passes

Change the Lambda query from string concatenation to:

```python
query = "SELECT name, category, price FROM products WHERE category = ?"
rows = cursor.execute(query, (category,)).fetchall()
```

Push the fix.

The expected flow is:

```text
deploy-preprod      PASS
aws-continuum-pentest PASS
deploy-prod         MANUAL / AVAILABLE
```

The production job then deploys the same artifact to a separate production hostname.

## 9. Important security controls

For a real enterprise implementation:

- Keep the Security Agent target non-production.
- Use GitLab OIDC, never static AWS keys.
- Pin the AWS Security Agent GitLab component to a reviewed release rather than a moving reference.
- Restrict the OIDC trust `sub` claim to the exact project and branch/environment.
- Restrict who can override GitLab pipeline variables; otherwise a user who can run a pipeline may be able to lower the gate threshold.
- Treat scope conflicts as blocking for release gates.
- Keep `fail-on-error` enabled so infrastructure failures do not silently promote code.
- Keep the artifact bucket private and encrypted.
- Prefer immutable artifact keys or object versioning.
- Use separate AWS accounts/OUs for POC and production.
- Do not put real secrets, customer data, or production credentials in the vulnerable application.
- Add CloudTrail, Security Hub, centralized logging and alerting around the CI/CD roles in an enterprise implementation.

## Official references

- AWS Security Agent CI/CD overview: https://docs.aws.amazon.com/securityagent/latest/userguide/cicd-pentest.html
- AWS GitLab tutorial: https://docs.aws.amazon.com/securityagent/latest/userguide/sample-cicd-gitlab.html
- AWS Security Agent quickstart: https://docs.aws.amazon.com/securityagent/latest/userguide/quickstart.html
- GitLab AWS OIDC: https://docs.gitlab.com/ci/cloud_services/aws/

AWS currently describes the CI/CD integration as **public preview**, so validate the component version and inputs against the current AWS documentation before using this outside the POC.
