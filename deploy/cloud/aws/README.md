# The platform tier on AWS

The commands that create the cloud host for the platform tier (D-129, Stage 36), in order, so that someone who was not there can build it again. Nothing here is needed to run Meridian: the deployment of record is one machine with `docker compose up` (D-130), and this host runs exactly that.

**What the host is**

- **Instance:** one arm64 instance in `ap-south-1`, `t4g.small` with 2 vCPUs and 2 GB plus 4 GB of swap, running Ubuntu 24.04. The platform image is published for arm64 (D-113). An account on AWS's Free plan may launch only free-tier types, which `t4g.small` is and `t4g.medium` is not; move to `t4g.medium` (4 GB) when the host's memory says so, by stopping the instance and changing its type.
- **Disk:** a 40 GB encrypted gp3 root volume, **kept if the instance is deleted**, because the database lives on it.
- **Inbound:** none. The Cloudflare tunnel dials out (D-088), and the shell is AWS Systems Manager's Session Manager, so there is no SSH key and no port 22.
- **Instance metadata:** requires a token (IMDSv2), with a hop limit of 1. Containers cannot reach the instance's role, only the host can.
- **Termination protection** is on.

The host is created with the AWS CLI v2 and an administrator profile, never the root user. Session Manager also needs its [plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) on the machine you connect from.

```bash
export AWS_REGION=ap-south-1
NAME=meridian-platform
```

## 1. The role Session Manager needs

```bash
aws iam create-role --role-name "$NAME-ssm" \
  --assume-role-policy-document file://deploy/cloud/aws/ec2-trust.json
aws iam attach-role-policy --role-name "$NAME-ssm" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name "$NAME-ssm"
aws iam add-role-to-instance-profile --instance-profile-name "$NAME-ssm" --role-name "$NAME-ssm"
```

## 2. A security group with no inbound rule

A new group allows all outbound traffic and no inbound traffic. Nothing is added to it.

```bash
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 create-security-group --group-name "$NAME" --vpc-id "$VPC" \
  --description "Meridian platform: no inbound; the tunnel dials out" \
  --query GroupId --output text)
```

## 3. Create the instance

The image is Canonical's current Ubuntu 24.04 arm64, looked up through its public parameter rather than pinned by id, because the id changes with every release.

```bash
AMI=$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
SUBNET=$(aws ec2 describe-subnets \
  --filters Name=vpc-id,Values="$VPC" Name=availability-zone,Values="${AWS_REGION}a" \
  --query 'Subnets[0].SubnetId' --output text)

ID=$(aws ec2 run-instances \
  --image-id "$AMI" --instance-type t4g.small --subnet-id "$SUBNET" \
  --security-group-ids "$SG" --associate-public-ip-address \
  --iam-instance-profile Name="$NAME-ssm" \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1 \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":40,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":false}}]' \
  --disable-api-termination \
  --user-data file://deploy/cloud/aws/cloud-init.yaml \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
                       "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-status-ok --instance-ids "$ID"
```

**The public IPv4 address is for outbound traffic only:** image pulls, Cloudflare, the ingest sources. Nothing listens on it, and the tunnel does not care when it changes, so there is no Elastic IP.

## 4. A budget alert

The host runs on credit. A budget emails the account's owner before the credit is spent, at 50%, 80% and 100% of a monthly limit that is set here and written nowhere in this repository. It counts spending **before** credits are applied: a budget that counts credits sees a net cost near zero and never warns.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
LIMIT=...   # the monthly limit, in USD
EMAIL=...   # where the warnings go
for pct in 50 80 100; do NOTES+="{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":$pct,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$EMAIL\"}]},"; done
aws budgets create-budget --account-id "$ACCOUNT" \
  --budget "{\"BudgetName\":\"$NAME\",\"BudgetLimit\":{\"Amount\":\"$LIMIT\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\",\"CostTypes\":{\"IncludeCredit\":false,\"IncludeRefund\":false}}" \
  --notifications-with-subscribers "[${NOTES%,}]"
```

## 5. Connect

```bash
aws ssm start-session --target "$ID"
sudo cloud-init status --wait     # done when it prints "status: done"
sudo -iu meridian
cd /opt/meridian
```

## 6. Bring the platform up

As `meridian`, in `/opt/meridian`, following `docs/OPERATIONS.md` § Bring-up. Every `change-me` left in `deploy/.env` is a new random value, the public hostname is set, and the generated secrets move to files:

```bash
cp deploy/.env.example deploy/.env
while grep -q '=change-me$' deploy/.env; do
  sed -i "0,/=change-me$/s//=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')/" deploy/.env
done
sed -i 's|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://dash.meridian.org.in|; s|^TUNNEL_HOSTNAME=.*|TUNNEL_HOSTNAME=dash.meridian.org.in|' deploy/.env
python3 deploy/tools/rotate_secret.py init
python3 deploy/tools/rotate_secret.py set tunnel_token    # paste the token, then Enter and Ctrl-D
cp deploy/ingest.live.toml.example deploy/ingest.toml
sed -i 's|^raw_root = .*|raw_root = "/var/lib/meridian-ingest/raw"|' deploy/ingest.toml
install -d -m 700 deploy/secrets/ingest
```

The tunnel token comes from Cloudflare Zero Trust → Networks → Tunnels → `meridian`. Paste it at the prompt, never on a command line, where the shell's history would keep it. The tunnel's route for the dash hostname must be `http://front:8080`.

```bash
alias compose='docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.secrets.yml -f deploy/docker-compose.public.yml -f deploy/docker-compose.demo.yml -f deploy/docker-compose.ingest.yml'
compose pull
compose --profile demo up -d --wait
```

The demonstration fleet runs alongside the platform so the public views always show a fleet. Every figure from it is badged simulated (D-330). `ingest` is built on the host, because no registry publishes it (D-352). It starts with every source the live settings enable; before each source's first fetch, re-read its terms page in `ATTRIBUTION.md`, and record the fetch in `docs/OPERATIONS.md` § Element sets and the archive, and every source live. The image is pulled from GHCR, so the package must be public, or `docker login ghcr.io` must be run first (`docs/OPERATIONS.md` § Pull or build).

Then, once it answers, leave the `meridian` shell (`exit`) and, as the default user, start the timers cloud-init installed and check the public surface:

```bash
sudo systemctl enable --now meridian-backup.timer meridian-restore-drill.timer
python3 /opt/meridian/deploy/tools/verify_public_surface.py https://dash.meridian.org.in --burst 300
```

## Moving the host

Credit is per account and cannot be moved, so the platform moves instead, by `docs/OPERATIONS.md` § Restoring on another machine:

1. Build the new host from this file in the new account.
2. Restore the newest dump onto it, with the same token pepper, and unpack the newest copy of the ingest raw store into its `ingest-raw` volume.
3. Copy the tunnel token across, then stop the old host's stack. The tunnel follows whichever connector is running.

Stations notice nothing but a short gap, and they resend what was not acknowledged (D-129).
