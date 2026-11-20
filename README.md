# AWS Security Agent + GitLab CI/CD POC

A minimal, isolated proof-of-concept showing:

**Git push → build → CloudFormation deploy to pre-prod → AWS Security Agent scoped penetration test → gate → manual production promotion**

The application intentionally contains a SQL injection vulnerability so the first run should produce a `HIGH` finding and block production. After fixing the query with a parameterized statement, the next run should pass.

> **Safety:** Deploy this only to an isolated AWS account/environment. Do not connect it to production data or expose it as a real application. The vulnerability is intentional.

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

1. Create an Agent Space.
2. Verify `pentest-preprod.example.com` for penetration testing. Because it is in Route 53, use the Route 53 verification option when available.
3. Create a penetration test.
4. Target:
   `https://pentest-preprod.example.com`
5. Authentication:
   none.
6. Network:
   public.
7. Keep the scope limited to the target.
8. Enable CI/CD / continuous testing.
9. Note:
   - Agent Space ID: `as-...`
   - Penetration test ID: `pt-...`
10. Connect the GitLab repository as the source-control integration.

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

Also configure the GitLab project maximum job timeout to be greater than the Security Agent gate's `job-timeout`.

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

Run the pipeline and verify authentication, Region, GitLab source-control integration and configuration.

Then remove the `dry-run` input and push again.

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
